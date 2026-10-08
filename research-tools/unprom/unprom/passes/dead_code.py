"""Remove obviously dead code.

  * statements after an unconditional ``return`` / ``break`` in the same block
  * empty ``do ... end`` blocks
  * ``if false`` / ``while false`` are handled by :mod:`constant_fold`
"""

from __future__ import annotations

from .. import ast_nodes as A


_TERMINATORS = (A.Return, A.Break, A.Continue, A.Goto)


class _Dead(A.NodeTransformer):
    def __init__(self, ctx):
        self.ctx = ctx

    def _prune_block(self, block: A.Block):
        out = []
        for st in block.stmts:
            out.append(st)
            if isinstance(st, _TERMINATORS):
                break
        if len(out) != len(block.stmts):
            self.ctx.bump("dead_stmts_removed", len(block.stmts) - len(out))
        block.stmts = out

    def visit_Block(self, node: A.Block):
        node = self.generic_visit(node)
        self._prune_block(node)
        return node

    def visit_Do(self, node: A.Do):
        node = self.generic_visit(node)
        if not node.body.stmts:
            self.ctx.bump("empty_do_removed")
            return None
        return node

    def visit_If(self, node: A.If):
        node = self.generic_visit(node)
        # collapse `if c then X else X end` -> X (only when X has no control-flow
        # effect that depends on the branch and c has no side effects)
        if len(node.clauses) == 1 and node.orelse is not None \
                and _same(node.clauses[0][1].stmts, node.orelse.stmts) \
                and not _has_calls(node.clauses[0][0]):
            self.ctx.bump("identical_if_branches_collapsed")
            return list(A.clone(node.orelse).stmts)
        # drop `if c then end` with no bodies (keep c if it may have effects)
        bodies_empty = all(not b.stmts for _c, b in node.clauses) \
            and (node.orelse is None or not node.orelse.stmts)
        if bodies_empty:
            keep = [A.CallStat(call=c) for c, _b in node.clauses
                    if isinstance(c, (A.Call, A.MethodCall))]
            self.ctx.bump("empty_if_removed")
            return keep or None
        return node


def _has_calls(node: A.Node) -> bool:
    return any(isinstance(n, (A.Call, A.MethodCall)) for n in A.walk(node))


def _same(a, b) -> bool:
    if len(a) != len(b) or not a:
        return False
    try:
        return _dump(a) == _dump(b)
    except Exception:  # noqa: BLE001
        return False


def _dump(stmts) -> str:
    from ..unparser import Unparser
    u = Unparser()
    for s in stmts:
        u._stat(s)
    return "\n".join(u.out)


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    for _ in range(4):
        d = _Dead(ctx)
        chunk = d.visit(chunk)
    return chunk
