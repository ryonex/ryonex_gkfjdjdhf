"""Constant folding / partial evaluation.

Reverses ``NumbersToExpressions`` (``5`` -> ``2 + 3`` -> ``5`` again) and the
``strcat`` form of ``SplitStrings`` (``"a" .. "b" .. "c"`` -> ``"abc"``).  Also
simplifies constant conditions, ``not not x``, redundant parens and the
``(cond and A or B)`` ternary when ``cond`` is constant.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..evaluator import const_value, value_to_node, is_truthy, LuaNil


class _Fold(A.NodeTransformer):
    def __init__(self, ctx):
        self.ctx = ctx
        self.changed = False

    # -- expressions ------------------------------------------------
    def visit_BinOp(self, node: A.BinOp):
        node.left = self.visit(node.left)
        node.right = self.visit(node.right)

        ok, val = const_value(node)
        if ok and _liftable(val):
            self.changed = True
            self.ctx.bump("folded_exprs")
            return _with_line(value_to_node(val), node)

        # (K and A or B)  ->  A or B depending on K, even when A/B aren't const
        if node.op == "or" and isinstance(node.left, A.BinOp) and node.left.op == "and":
            okc, cond = const_value(node.left.left)
            if okc:
                self.changed = True
                if is_truthy(cond):
                    oka, _ = const_value(node.left.right)
                    # A is only the result when A is truthy; keep semantics safe
                    # by only collapsing when A is a constant-truthy value.
                    if oka and is_truthy(_):
                        return node.left.right
                    return A.BinOp(op="or", left=node.left.right, right=node.right,
                                   line=node.line)
                return node.right
        return node

    def visit_UnOp(self, node: A.UnOp):
        node.operand = self.visit(node.operand)
        ok, val = const_value(node)
        if ok and _liftable(val):
            self.changed = True
            self.ctx.bump("folded_exprs")
            return _with_line(value_to_node(val), node)
        if node.op == "not" and isinstance(node.operand, A.UnOp) \
                and node.operand.op == "not" \
                and isinstance(node.operand.operand, A.UnOp) \
                and node.operand.operand.op == "not":
            self.changed = True
            return node.operand.operand
        return node

    def visit_Paren(self, node: A.Paren):
        node.inner = self.visit(node.inner)
        inner = node.inner
        # Parens are only semantically meaningful around multi-value exprs.
        if isinstance(inner, (A.Number, A.String, A.Nil, A.TrueExpr, A.FalseExpr,
                              A.Name, A.Index, A.Paren)):
            self.changed = True
            return inner
        return node

    # -- statements ------------------------------------------------
    def visit_If(self, node: A.If):
        node = self.generic_visit(node)
        new_clauses = []
        for cond, body in node.clauses:
            ok, val = const_value(cond)
            if ok:
                if is_truthy(val):
                    # This clause always fires: everything after is dead.
                    if not new_clauses:
                        self.changed = True
                        return _inline_block(body)
                    node.orelse = body
                    node.clauses = new_clauses
                    return node
                else:
                    self.changed = True
                    continue  # drop this clause
            new_clauses.append((cond, body))
        if not new_clauses:
            self.changed = True
            return _inline_block(node.orelse) if node.orelse else A.Do(body=A.Block([]))
        node.clauses = new_clauses
        return node

    def visit_While(self, node: A.While):
        node = self.generic_visit(node)
        ok, val = const_value(node.cond)
        if ok and not is_truthy(val):
            self.changed = True
            return None
        return node


def _inline_block(block: A.Block):
    return A.Do(body=block)


def _liftable(v) -> bool:
    return isinstance(v, (bool, float, str)) or v is LuaNil


def _with_line(new: A.Node, old: A.Node) -> A.Node:
    new.line = getattr(old, "line", 0)
    return new


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    for _ in range(12):
        f = _Fold(ctx)
        chunk = f.visit(chunk)
        if not f.changed:
            break
    return chunk
