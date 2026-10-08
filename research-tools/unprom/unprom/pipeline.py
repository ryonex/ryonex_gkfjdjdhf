"""Pass pipeline: parse -> run passes -> unparse."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import ast_nodes as A
from .parser import parse
from .unparser import unparse


@dataclass
class PassContext:
    """Shared scratch space + reporting for a single deobfuscation run."""
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)
    options: Dict[str, object] = field(default_factory=dict)

    def note(self, msg: str):
        self.notes.append(msg)

    def bump(self, key: str, n: int = 1):
        self.stats[key] = self.stats.get(key, 0) + n


# A pass is a callable (Chunk, PassContext) -> Chunk
Pass = Callable[[A.Chunk, PassContext], A.Chunk]


def _load_passes() -> Dict[str, Pass]:
    from .passes import anti_tamper, constant_array, constant_fold
    from .passes import encrypt_strings, wrap_in_function, proxify_locals
    from .passes import dead_code, devirtualize, vm_lift, rename, cleanup

    return {
        "unwrap": wrap_in_function.run,
        "anti_tamper": anti_tamper.run,
        "encrypt_strings": encrypt_strings.run,
        "constant_array": constant_array.run,
        "vm_lift": vm_lift.run,
        "devirtualize": devirtualize.run,
        "constant_fold": constant_fold.run,
        "proxify_locals": proxify_locals.run,
        "dead_code": dead_code.run,
        "cleanup": cleanup.run,
        "rename": rename.run,
    }


# Order matters. Structural unwrapping first, then string/const recovery, then
# the VM lifter, then simplification, then cosmetic renaming last.
DEFAULT_PASSES: List[str] = [
    "unwrap",
    "anti_tamper",
    "constant_fold",   # fold obfuscated arithmetic first so detectors see literals
    "encrypt_strings",
    "constant_array",
    "constant_fold",
    "vm_lift",         # full VM -> structured Lua (falls back to devirtualize)
    "devirtualize",    # de-flatten whatever vm_lift left behind
    "constant_fold",
    "proxify_locals",
    "constant_fold",
    "dead_code",
    "cleanup",
    "rename",
]


class Pipeline:
    def __init__(self, passes: Optional[List[str]] = None,
                 options: Optional[Dict[str, object]] = None):
        self.registry = _load_passes()
        self.passes = list(passes if passes is not None else DEFAULT_PASSES)
        self.options = options or {}

    def available(self) -> List[str]:
        return sorted(self.registry)

    def run_ast(self, chunk: A.Chunk) -> tuple[A.Chunk, PassContext]:
        ctx = PassContext(options=dict(self.options))
        for name in self.passes:
            fn = self.registry.get(name)
            if fn is None:
                ctx.note(f"[pipeline] unknown pass {name!r}, skipped")
                continue
            try:
                chunk = fn(chunk, ctx) or chunk
            except Exception as exc:  # keep going; a failed pass shouldn't kill the run
                ctx.note(f"[{name}] failed: {exc!r}")
        return chunk, ctx

    def run(self, source: str) -> "tuple[str, PassContext]":
        chunk = parse(source)
        chunk, ctx = self.run_ast(chunk)
        indent = str(self.options.get("indent", "  ")) or "  "
        return unparse(chunk, indent=indent), ctx


def deobfuscate(source: str, passes: Optional[List[str]] = None,
                options: Optional[Dict[str, object]] = None) -> str:
    """Convenience wrapper: obfuscated source in, cleaned source out."""
    text, _ctx = Pipeline(passes, options).run(source)
    return text
