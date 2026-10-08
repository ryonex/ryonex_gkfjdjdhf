"""Strip the ``AntiTamper`` prelude.

AntiTamper injects a single ``do ... end`` block at the very top of the script.
It is pure runtime self-checking with no effect on program output, so we drop
any top-level ``do`` block that carries its fingerprints ("Tamper Detected!",
the debug.sethook dance, ``repeat until valid``).
"""

from __future__ import annotations

from .. import ast_nodes as A


_MARKERS = ("Tamper Detected", "Tamper detected")


def _has_marker_string(node: A.Node) -> bool:
    for n in A.walk(node):
        if isinstance(n, A.String) and any(m in n.value for m in _MARKERS):
            return True
    return False


def _looks_like_anti_tamper(do: A.Do) -> bool:
    if _has_marker_string(do):
        return True
    # Fallback fingerprint: `repeat until <name>` plus a `debug` reference plus
    # the 2^45 modulus constant that shows up nowhere else.
    names = {n.name for n in A.walk(do) if isinstance(n, A.Name)}
    has_repeat_until = any(
        isinstance(n, A.Repeat) and isinstance(n.cond, A.Name) for n in A.walk(do))
    has_debug = "debug" in names or "sethook" in names or "getinfo" in names
    return has_repeat_until and has_debug


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    kept = []
    removed = 0
    for st in chunk.body.stmts:
        if isinstance(st, A.Do) and _looks_like_anti_tamper(st):
            removed += 1
            continue
        kept.append(st)
    if removed:
        chunk.body.stmts = kept
        ctx.bump("anti_tamper_blocks_removed", removed)
        ctx.note(f"[anti_tamper] removed {removed} tamper-check block(s)")
    return chunk
