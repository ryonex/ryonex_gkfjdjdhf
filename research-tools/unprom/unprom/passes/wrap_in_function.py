"""Reverse ``WrapInFunction``.

Prometheus wraps the whole script as::

    return (function(...) <body> end)(...)

possibly several times.  We peel every such layer off the top of the chunk.
"""

from __future__ import annotations

from .. import ast_nodes as A


def _unwrap_once(chunk: A.Chunk) -> bool:
    stmts = chunk.body.stmts
    if len(stmts) != 1 or not isinstance(stmts[0], A.Return):
        return False
    ret = stmts[0]
    if len(ret.values) != 1:
        return False
    call = ret.values[0]
    if isinstance(call, A.Paren):
        call = call.inner
    if not isinstance(call, A.Call):
        return False
    fn = call.func
    if isinstance(fn, A.Paren):
        fn = fn.inner
    if not isinstance(fn, A.Function):
        return False
    # Args must be exactly ``...`` (or empty) so hoisting keeps semantics.
    if not all(isinstance(a, A.Vararg) for a in call.args):
        return False
    if any(not isinstance(p, A.Name) for p in fn.params):
        return False
    # Any non-vararg params would need the call args; WrapInFunction never adds
    # them, so bail if present.
    if fn.params:
        return False
    chunk.body = fn.body
    return True


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    n = 0
    while _unwrap_once(chunk):
        n += 1
    if n:
        ctx.bump("wrap_layers_removed", n)
        ctx.note(f"[unwrap] removed {n} WrapInFunction layer(s)")
    return chunk
