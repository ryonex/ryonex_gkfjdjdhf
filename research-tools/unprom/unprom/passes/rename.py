"""Give obfuscated local identifiers readable names.

Modes (``ctx.options['rename']``):
  * ``none`` - do nothing
  * ``auto`` - rename only names that look machine-generated (default)
  * ``all``  - rename every local to ``v1``, ``v2`` ...

New names are globally unique and are checked against every identifier already
present in the tree, so renaming can never introduce shadowing.
"""

from __future__ import annotations

import re

from .. import ast_nodes as A
from ..scope import resolve

_OBFUSCATED = [
    re.compile(r"^_\d+$"),                 # 'number' generator  (PREFIX .. id)
    re.compile(r"^[Il1]{3,}$"),            # 'Il' generator
    re.compile(r"^[O0lI1]{4,}$"),
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]{13,}$"),   # very long mangled
]


def _looks_obfuscated(name: str) -> bool:
    if any(p.match(name) for p in _OBFUSCATED):
        return True
    if any(ord(c) > 127 for c in name):
        return True
    return False


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    mode = str(ctx.options.get("rename", "auto"))
    if mode == "none":
        return chunk

    r = resolve(chunk)
    used = {n.name for n in A.walk(chunk) if isinstance(n, A.Name)}
    used.update({"self", "_ENV", "_G"})

    counter = 0

    def fresh() -> str:
        nonlocal counter
        while True:
            counter += 1
            cand = f"v{counter}"
            if cand not in used:
                used.add(cand)
                return cand

    mapping = {}
    for bid, b in r.bindings.items():
        if b.kind == "param" and b.name == "self":
            continue
        if mode == "all" or _looks_obfuscated(b.name):
            mapping[bid] = fresh()

    if not mapping:
        return chunk

    for n in A.walk(chunk):
        if isinstance(n, A.Name) and n.binding in mapping:
            n.name = mapping[n.binding]

    ctx.bump("locals_renamed", len(mapping))
    ctx.note(f"[rename] renamed {len(mapping)} local(s) (mode={mode})")
    return chunk
