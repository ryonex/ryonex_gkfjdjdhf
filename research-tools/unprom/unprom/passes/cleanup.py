"""Cosmetic tidying that is always safe.

  * inline ``do ... end`` blocks that declare no locals of their own
  * drop unused ``local x = <side-effect-free>`` declarations
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..astutil import count_binding_uses
from ..scope import resolve


_PURE = (A.Number, A.String, A.Nil, A.TrueExpr, A.FalseExpr, A.Name,
         A.Function, A.Vararg)


def _is_pure(expr: A.Node) -> bool:
    if isinstance(expr, _PURE):
        return True
    if isinstance(expr, A.Paren):
        return _is_pure(expr.inner)
    if isinstance(expr, (A.BinOp,)):
        return _is_pure(expr.left) and _is_pure(expr.right)
    if isinstance(expr, A.UnOp):
        return _is_pure(expr.operand)
    if isinstance(expr, A.Table):
        return all(_is_pure(v) and (k is None or not isinstance(k, A.Node) or _is_pure(k))
                   for _kind, k, v in expr.fields)
    return False


class _InlineDo(A.NodeTransformer):
    def __init__(self, ctx):
        self.ctx = ctx

    def _declares_local(self, block: A.Block) -> bool:
        for st in block.stmts:
            if isinstance(st, (A.LocalAssign, A.LocalFunction, A.Label)):
                return True
        return False

    def visit_Block(self, node: A.Block):
        node = self.generic_visit(node)
        out = []
        for st in node.stmts:
            if isinstance(st, A.Do) and not self._declares_local(st.body):
                out.extend(st.body.stmts)
                self.ctx.bump("do_blocks_inlined")
            else:
                out.append(st)
        node.stmts = _merge_decl_assign(out, self.ctx)
        return node


def _merge_decl_assign(stmts, ctx):
    """`local x`  immediately followed by  `x = <expr>`  ->  `local x = <expr>`
    (only for a single, un-initialised name and a plain-Name assignment)."""
    out = []
    i = 0
    while i < len(stmts):
        s = stmts[i]
        nxt = stmts[i + 1] if i + 1 < len(stmts) else None
        if isinstance(s, A.LocalAssign) and len(s.targets) == 1 and not s.values \
                and isinstance(nxt, A.Assign) and len(nxt.targets) == 1 \
                and len(nxt.values) == 1 \
                and isinstance(nxt.targets[0], A.Name) \
                and nxt.targets[0].name == s.targets[0].name \
                and not _mentions(nxt.values[0], s.targets[0].name):
            out.append(A.LocalAssign(targets=s.targets, values=[nxt.values[0]],
                                     attribs=s.attribs))
            ctx.bump("decl_assign_merged")
            i += 2
            continue
        out.append(s)
        i += 1
    return out


def _mentions(node: A.Node, name: str) -> bool:
    return any(isinstance(n, A.Name) and n.name == name for n in A.walk(node))


def _drop_unused_locals(chunk: A.Chunk, ctx) -> bool:
    resolve(chunk)
    changed = False

    class _Drop(A.NodeTransformer):
        def visit_LocalAssign(self, node: A.LocalAssign):
            node = self.generic_visit(node)
            if not node.targets or len(node.targets) != len(node.values):
                return node
            keep_t, keep_v = [], []
            for t, v in zip(node.targets, node.values):
                uses = count_binding_uses(chunk, t.binding)
                if uses <= 1 and _is_pure(v):
                    nonlocal changed
                    changed = True
                    ctx.bump("unused_locals_removed")
                    continue
                keep_t.append(t)
                keep_v.append(v)
            if not keep_t:
                return None
            node.targets, node.values = keep_t, keep_v
            return node

    chunk2 = _Drop().visit(chunk)
    chunk.body = chunk2.body
    return changed


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    chunk = _InlineDo(ctx).visit(chunk)
    for _ in range(5):
        if not _drop_unused_locals(chunk, ctx):
            break
    return chunk
