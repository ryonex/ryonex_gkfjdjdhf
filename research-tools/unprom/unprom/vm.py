"""Shared analysis of the Prometheus ``Vmify`` container.

This module locates the VM container function, flattens its balanced
``if pos < K`` search tree into an ordered block list, and builds a basic-block
control-flow graph.  Both :mod:`unprom.passes.devirtualize` (de-flatten only)
and :mod:`unprom.passes.vm_lift` (full lift) build on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ast_nodes as A
from .astutil import as_int, unparen


# ======================================================================
# Container discovery
# ======================================================================
@dataclass
class Container:
    outer_fn: Optional[A.Function]      # (env, unpack, newproxy, setmeta, getmeta, select, arg, ...)
    container_fn: A.Function            # C(pos, args, upvals, gc)
    while_stmt: A.While
    pos_name: str
    args_name: str
    upvals_name: str
    gc_name: str
    ret_name: Optional[str]
    env_name: Optional[str]
    unpack_name: Optional[str]
    select_name: Optional[str]
    reg_names: List[str] = field(default_factory=list)
    factory_names: set = field(default_factory=set)      # createClosure* var names
    container_name: Optional[str] = None
    alloc_upval_name: Optional[str] = None
    upvals_table_name: Optional[str] = None
    free_upval_name: Optional[str] = None
    free_upval_names: set = field(default_factory=set)
    start_id: Optional[int] = None


def find_container(chunk: A.Chunk) -> Optional[Container]:
    cand = _find_container_fn(chunk)
    if cand is None:
        return None
    fn, wh = cand
    params = [p.name for p in fn.params]
    pos_name = wh.cond.name
    c = Container(
        outer_fn=None, container_fn=fn, while_stmt=wh, pos_name=pos_name,
        args_name=params[1] if len(params) > 1 else "args",
        upvals_name=params[2] if len(params) > 2 else "upvals",
        gc_name=params[3] if len(params) > 3 else "gc",
        ret_name=None, env_name=None, unpack_name=None, select_name=None,
    )
    _fill_return(c)
    _fill_registers(c)
    _fill_outer(chunk, c)
    return c


def _find_container_fn(chunk: A.Chunk) -> Optional[Tuple[A.Function, A.While]]:
    best = None
    for n in A.walk(chunk):
        if not isinstance(n, A.Function) or n.body is None:
            continue
        for st in n.body.stmts:
            if not isinstance(st, A.While) or not isinstance(st.cond, A.Name):
                continue
            leaves = _count_leaves(st.body, st.cond.name)
            if leaves is None or leaves < 3:
                continue
            if best is None or leaves > best[2]:
                best = (n, st, leaves)
    return (best[0], best[1]) if best else None


def _fill_return(c: Container):
    body = c.container_fn.body.stmts
    for st in body:
        if isinstance(st, A.Return) and len(st.values) == 1:
            call = unparen(st.values[0])
            if isinstance(call, A.Call) and len(call.args) == 1 \
                    and isinstance(unparen(call.args[0]), A.Name):
                c.unpack_name = unparen(call.func).name if isinstance(
                    unparen(call.func), A.Name) else None
                c.ret_name = unparen(call.args[0]).name
                return


def _fill_registers(c: Container):
    for st in c.container_fn.body.stmts:
        if isinstance(st, A.LocalAssign) and not st.values and len(st.targets) >= 1:
            names = [t.name for t in st.targets]
            if c.ret_name in names or len(names) > 3:
                c.reg_names = names
                return
    # fallback: first empty local decl
    for st in c.container_fn.body.stmts:
        if isinstance(st, A.LocalAssign) and not st.values:
            c.reg_names = [t.name for t in st.targets]
            return


def _fill_outer(chunk: A.Chunk, c: Container):
    """Top-level `return (function(env, unpack, newproxy, setmetatable,
    getmetatable, select, arg, ...) ... end)(...)`."""
    for st in chunk.body.stmts:
        if not isinstance(st, A.Return) or len(st.values) != 1:
            continue
        call = unparen(st.values[0])
        if not isinstance(call, A.Call):
            continue
        fn = unparen(call.func)
        if not isinstance(fn, A.Function) or len(fn.params) < 6:
            continue
        c.outer_fn = fn
        names = [p.name for p in fn.params]
        c.env_name = names[0]
        c.unpack_name = c.unpack_name or names[1]
        c.select_name = names[5]
        _fill_factories_and_helpers(fn, c)
        _fill_start_id(fn, c)
        return


def _fill_factories_and_helpers(outer: A.Function, c: Container):
    """The outer body assigns a batch of locals; classify the interesting ones."""
    pairs = []
    for st in A.walk(outer):
        if isinstance(st, A.Assign):
            for tgt, val in zip(st.targets, st.values):
                name = unparen(tgt).name if isinstance(unparen(tgt), A.Name) else None
                if name is not None:
                    pairs.append((name, unparen(val)))

    for name, val in pairs:
        if not isinstance(val, A.Function):
            continue
        if val is c.container_fn or _is_container_literal(val, c):
            c.container_name = name
        elif _is_factory(val):
            c.factory_names.add(name)
        elif _is_alloc_upval(val):
            c.alloc_upval_name = name
        elif _is_free_upval(val):
            c.free_upval_name = name
            c.free_upval_names.add(name)
    _guess_upvals_table(outer, c)


def _is_factory(fn: A.Function) -> bool:
    """`function(pos, ups) local g = p(ups); local E = function(...) return
    C(pos, {...}, ups, g) end; return E end` and its variants."""
    if len(fn.params) != 2:
        return False
    for st in A.walk(fn):
        if isinstance(st, A.Return) and len(st.values) == 1:
            v = unparen(st.values[0])
            if isinstance(v, A.Call) and len(v.args) >= 2 \
                    and isinstance(unparen(v.args[1]), A.Table):
                return True
    return False


def _is_container_literal(fn: A.Function, c: Container) -> bool:
    return [p.name for p in fn.params][:1] == [c.pos_name] and any(
        isinstance(s, A.While) for s in fn.body.stmts)


def _is_alloc_upval(fn: A.Function) -> bool:
    """`function() X = X + 1; V[X] = 1; return X end`."""
    if fn.params:
        return False
    inc = ret = None
    for st in A.walk(fn):
        if isinstance(st, A.Assign) and len(st.targets) == 1 \
                and isinstance(unparen(st.targets[0]), A.Name):
            x = unparen(st.targets[0]).name
            v = unparen(st.values[0]) if st.values else None
            if isinstance(v, A.BinOp) and v.op == "+" and (
                    _is_named(v.left, x) or _is_named(v.right, x)):
                inc = x
        if isinstance(st, A.Return) and len(st.values) == 1 \
                and isinstance(unparen(st.values[0]), A.Name):
            ret = unparen(st.values[0]).name
    return inc is not None and inc == ret


def _is_free_upval(fn: A.Function) -> bool:
    """`function(o) V[o] = V[o] - 1; if 0 == V[o] then V[o], t[o] = nil, nil end end`
    (or the list variant)."""
    if len(fn.params) != 1:
        return False
    has_dec = any(isinstance(n, A.BinOp) and n.op == "-" for n in A.walk(fn))
    has_nilnil = any(
        isinstance(n, A.Assign) and len(n.values) >= 2
        and all(isinstance(unparen(v), A.Nil) for v in n.values)
        for n in A.walk(fn))
    return has_dec and has_nilnil


def _is_named(node, name) -> bool:
    node = unparen(node)
    return isinstance(node, A.Name) and node.name == name


def _stmt_names(node) -> List[str]:
    return [n.name for n in A.walk(node) if isinstance(n, A.Name)]


def _guess_upvals_table(outer: A.Function, c: Container):
    # look inside container blocks for  X[<expr>]  where X is a free outer local
    counts: Dict[str, int] = {}
    for n in A.walk(c.container_fn):
        if isinstance(n, A.Index) and isinstance(unparen(n.obj), A.Name):
            nm = unparen(n.obj).name
            if nm not in c.reg_names and nm not in (
                    c.pos_name, c.args_name, c.upvals_name, c.gc_name, c.ret_name,
                    c.env_name):
                counts[nm] = counts.get(nm, 0) + 1
    if counts:
        c.upvals_table_name = max(counts, key=counts.get)


def _fill_start_id(outer: A.Function, c: Container):
    for n in A.walk(outer):
        if isinstance(n, A.Call):
            inner = unparen(n.func)
            if isinstance(inner, A.Call) and len(inner.args) == 2:
                a0 = as_int(inner.args[0])
                if a0 is not None and isinstance(unparen(inner.args[1]), A.Table):
                    c.start_id = a0
                    return


# ======================================================================
# Block flattening
# ======================================================================
def split_if(block: A.Block, pos: str):
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


def _count_leaves(block: A.Block, pos: str):
    sp = split_if(block, pos)
    if sp is None:
        return 1
    _b, l, r = sp
    ln = _count_leaves(l, pos)
    rn = _count_leaves(r, pos)
    if ln is None or rn is None:
        return None
    return ln + rn


def flatten_blocks(wh: A.While) -> Tuple[List[List[A.Node]], List[int]]:
    leaves: List[List[A.Node]] = []
    seps: List[int] = []

    def rec(block: A.Block):
        sp = split_if(block, wh.cond.name)
        if sp is None:
            leaves.append(list(block.stmts))
            return
        bound, l, r = sp
        rec(l)
        seps.append(bound)
        rec(r)

    rec(wh.body)
    return leaves, seps


def id_to_index(idv: int, seps: List[int]) -> int:
    lo = 0
    for i, s in enumerate(seps):
        if idv < s:
            return i
        lo = i + 1
    return lo
