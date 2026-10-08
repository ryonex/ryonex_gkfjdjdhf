"""Best-effort handling of the ``Vmify`` step.

Full devirtualisation (lifting the custom bytecode back to the original Lua) is
a research-grade problem and version-specific.  What this pass does instead is
**de-flatten the VM dispatcher**, which recovers most of the readability:

  * Prometheus compiles every function into numbered *blocks* run by a single
    ``while pos do ... end`` loop whose body is a balanced binary search tree of
    ``if pos < K then ... else ... end`` over shuffled block ids.
  * We reconstruct the block list in order, map the opaque ids to small indices
    (0, 1, 2, ...), rewrite every ``pos = <id>`` transition accordingly, and
    replace the search tree with a flat ``if pos == 0 then ... elseif ...``
    chain.

The result is the same VM, but with linear, readable dispatch and real block
numbers - a much better starting point for manual analysis.  Register locals and
the upvalue / closure scaffolding are left intact.  Set the ``devirt`` option to
``"off"`` to skip this entirely.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .. import ast_nodes as A
from ..astutil import as_int, unparen


# --------------------------------------------------------------------------
# Locating the container function
# --------------------------------------------------------------------------
def _find_container(chunk: A.Chunk) -> Optional[Tuple[A.Function, A.While]]:
    """A function whose body contains `while <pos> do <search-tree> end` where
    the search tree is nested `if <pos> < K then ... else ... end`."""
    best = None
    for n in A.walk(chunk):
        if not isinstance(n, A.Function) or n.body is None:
            continue
        for st in n.body.stmts:
            if not isinstance(st, A.While) or not isinstance(st.cond, A.Name):
                continue
            pos = st.cond.name
            leaves = _tree_leaves(st.body, pos)
            if leaves is None or leaves < 3:
                continue
            if best is None or leaves > best[2]:
                best = (n, st, leaves)
    if best is None:
        return None
    return best[0], best[1]


def _split_if(block: A.Block, pos: str):
    """Return (bound, left_block, right_block) if `block` is a lone
    `if pos < K then L else R end`, else None."""
    if len(block.stmts) != 1:
        return None
    st = block.stmts[0]
    if not (isinstance(st, A.If) and len(st.clauses) == 1 and st.orelse is not None):
        return None
    cond = st.clauses[0][0]
    if not (isinstance(cond, A.BinOp) and cond.op == "<"):
        return None
    left = unparen(cond.left)
    if not (isinstance(left, A.Name) and left.name == pos):
        return None
    bound = as_int(cond.right)
    if bound is None:
        return None
    return bound, st.clauses[0][1], st.orelse


def _tree_leaves(block: A.Block, pos: str):
    sp = _split_if(block, pos)
    if sp is None:
        # a straight-line leaf block
        return 1
    _bound, l, r = sp
    ln = _tree_leaves(l, pos)
    rn = _tree_leaves(r, pos)
    if ln is None or rn is None:
        return None
    return ln + rn


# --------------------------------------------------------------------------
# Flattening the search tree into ordered (bound, block) leaves
# --------------------------------------------------------------------------
class _Leaf:
    def __init__(self, stmts: List[A.Node]):
        self.stmts = stmts
        self.lo = None          # inclusive id lower bound
        self.hi = None          # exclusive id upper bound
        self.index = -1


def _collect(block: A.Block, pos: str, leaves: List[_Leaf], seps: List[int]):
    sp = _split_if(block, pos)
    if sp is None:
        leaves.append(_Leaf(list(block.stmts)))
        return
    bound, l, r = sp
    _collect(l, pos, leaves, seps)
    seps.append(bound)
    _collect(r, pos, leaves, seps)


def _flatten(wh: A.While):
    leaves: List[_Leaf] = []
    seps: List[int] = []
    _collect(wh.body, wh.cond.name, leaves, seps)
    # seps has len(leaves)-1 entries, sorted ascending, separating consecutive
    # leaves: id(leaf i-1) < seps[i-1] <= id(leaf i)
    bounds = [None] + seps + [None]
    for i, leaf in enumerate(leaves):
        leaf.lo = bounds[i]
        leaf.hi = bounds[i + 1]
        leaf.index = i
    return leaves, seps


def _id_to_index(idv: int, seps: List[int]) -> int:
    lo = 0
    for i, s in enumerate(seps):
        if idv < s:
            return i
        lo = i + 1
    return lo


# --------------------------------------------------------------------------
# Rewriting pos transitions
# --------------------------------------------------------------------------
class _Retarget(A.NodeTransformer):
    def __init__(self, pos_name: str, seps: List[int], nleaves: int, ctx):
        self.pos = pos_name
        self.seps = seps
        self.n = nleaves
        self.ctx = ctx

    def _map_num(self, node: A.Node) -> A.Node:
        iv = as_int(node)
        if iv is None:
            return node
        # Only remap plausible block ids (Prometheus uses 0 .. 2^24).
        if 0 <= iv <= (1 << 24):
            idx = _id_to_index(iv, self.seps)
            self.ctx.bump("vm_transitions_remapped")
            return A.Number(value=float(idx), line=getattr(node, "line", 0))
        return node

    def visit_Assign(self, node: A.Assign):
        node = self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(unparen(node.targets[0]), A.Name) \
                and unparen(node.targets[0]).name == self.pos and len(node.values) == 1:
            node.values[0] = self._map_value(node.values[0])
        return node

    def _map_value(self, v: A.Node) -> A.Node:
        v = unparen(v)
        if isinstance(v, A.Number):
            return self._map_num(v)
        if isinstance(v, A.BinOp) and v.op in ("and", "or"):
            v.left = self._map_value(v.left)
            v.right = self._map_value(v.right)
            return v
        return v


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    if str(ctx.options.get("devirt", "on")) in ("off", "false", "0"):
        return chunk
    found = _find_container(chunk)
    if not found:
        return chunk
    fn, wh = found
    pos_name = wh.cond.name
    leaves, seps = _flatten(wh)
    if len(leaves) < 2:
        return chunk

    ctx.note(f"[devirtualize] found Prometheus VM container: {len(leaves)} blocks, "
             f"pos var {pos_name!r}")

    # Remap every `pos = <id>` inside the blocks to a 0-based index.
    retarget = _Retarget(pos_name, seps, len(leaves), ctx)
    for leaf in leaves:
        leaf.stmts = [retarget.visit(s) for s in leaf.stmts]

    # Rebuild the loop body as a flat dispatch chain.
    clauses = []
    for leaf in leaves:
        cond = A.BinOp(op="==", left=A.Name(name=pos_name),
                       right=A.Number(value=float(leaf.index)))
        clauses.append((cond, A.Block(stmts=leaf.stmts)))
    wh.body = A.Block(stmts=[A.If(clauses=clauses, orelse=None)])

    # Remap the VM entry point in the outer `createClosure(<startId>, ...)` call.
    _remap_entry(chunk, seps, ctx)

    ctx.bump("vm_containers_deflattened")
    ctx.note("[devirtualize] de-flattened VM dispatch; this is the VM in "
             "readable form, not lifted source")
    return chunk


def _remap_entry(chunk: A.Chunk, seps: List[int], ctx):
    """The top-level `return F(startBlockId, {upvals})(unpack(arg))` -
    rewrite startBlockId to its 0-based index."""
    for n in A.walk(chunk):
        if not isinstance(n, A.Call):
            continue
        inner = unparen(n.func)
        if not (isinstance(inner, A.Call) and len(inner.args) == 2):
            continue
        a0 = as_int(inner.args[0])
        a1 = unparen(inner.args[1])
        if a0 is not None and 0 <= a0 <= (1 << 24) and isinstance(a1, A.Table):
            idx = _id_to_index(a0, seps)
            inner.args[0] = A.Number(value=float(idx))
            ctx.note(f"[devirtualize] VM entry block = {idx}")
            return
