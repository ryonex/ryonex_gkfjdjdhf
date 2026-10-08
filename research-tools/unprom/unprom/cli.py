"""Command-line entry point for ``unprom``.

Invoked as ``unprom`` (after ``pip install``), ``python -m unprom`` or
``python unprom.py``.
"""

from __future__ import annotations

import argparse
import sys

from .pipeline import Pipeline, DEFAULT_PASSES

_DESCRIPTION = """\
unprom - deobfuscator for the Prometheus Lua/Luau obfuscator (and its rebrands).

Examples:
  unprom obf.lua                        # -> obf.deob.lua
  unprom obf.lua -o clean.lua -v
  cat obf.lua | unprom - > clean.lua
  unprom obf.lua --passes unwrap,encrypt_strings,constant_fold
  unprom obf.lua --rename all --devirt deflatten
  unprom --list-passes
"""


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="unprom", description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?",
                    help="obfuscated .lua file, or - for stdin")
    ap.add_argument("-o", "--output",
                    help="write result here (default: <input>.deob.lua; - for stdout)")
    ap.add_argument("--passes",
                    help="explicit comma-separated pass list (see --list-passes)")
    ap.add_argument("--skip",
                    help="comma-separated passes to drop from the default pipeline")
    ap.add_argument("--rename", choices=["none", "auto", "all"], default="auto",
                    help="local-variable renaming aggressiveness (default: auto)")
    ap.add_argument("--devirt", choices=["lift", "deflatten", "off"], default="lift",
                    help="Vmify handling: lift to structured Lua (default), only "
                         "de-flatten the dispatcher, or leave the VM untouched")
    ap.add_argument("--no-devirt", action="store_true", help="alias for --devirt off")
    ap.add_argument("--indent", default="  ",
                    help="indentation string (default: two spaces)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print per-pass notes and statistics")
    ap.add_argument("--list-passes", action="store_true",
                    help="list available passes and the default order, then exit")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.list_passes:
        print("available passes:")
        for name in Pipeline().available():
            print(f"  {name}")
        print("\ndefault order:\n  " + " -> ".join(DEFAULT_PASSES))
        return 0

    if not args.input:
        ap.error("input file required (or - for stdin)")

    devirt = "off" if args.no_devirt else args.devirt

    if args.passes:
        passes = [p.strip() for p in args.passes.split(",") if p.strip()]
    else:
        passes = list(DEFAULT_PASSES)
        if args.skip:
            drop = {p.strip() for p in args.skip.split(",")}
            passes = [p for p in passes if p not in drop]
        if devirt == "off":
            passes = [p for p in passes if p not in ("vm_lift", "devirtualize")]
        elif devirt == "deflatten":
            passes = [p for p in passes if p != "vm_lift"]

    options = {"rename": args.rename, "devirt": devirt, "indent": args.indent}

    try:
        result, ctx = Pipeline(passes, options).run(_read(args.input))
    except FileNotFoundError:
        print(f"unprom: no such file: {args.input}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"unprom: fatal: {exc!r}", file=sys.stderr)
        return 2

    if args.output:
        out_path = args.output
    elif args.input == "-":
        out_path = "-"
    else:
        base = args.input[:-4] if args.input.endswith(".lua") else args.input
        out_path = base + ".deob.lua"

    if out_path == "-":
        sys.stdout.write(result)
    else:
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(result)
        print(f"unprom: wrote {out_path} ({len(result)} bytes)", file=sys.stderr)

    if args.verbose:
        print("\n--- pass notes ---", file=sys.stderr)
        for note in ctx.notes:
            print(note, file=sys.stderr)
        if ctx.stats:
            print("--- stats ---", file=sys.stderr)
            for key, val in sorted(ctx.stats.items()):
                print(f"  {key}: {val}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
