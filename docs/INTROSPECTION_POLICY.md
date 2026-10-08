# Introspection Guard — Security & Compatibility Policy

Scope: `engine.py` (`transform`, pre-build token scan). This policy replaces the
earlier "lexical identifier scan" assumption with an explicit statement of what
is guaranteed, what is rejected, and what remains unsupported.

## Why introspection is special

The Prometheus Vmify backend compiles the program into a register machine whose
closures capture the environment **once** (`getfenv and getfenv() or _ENV`) and
simulate upvalues with a shared table. As a result:

- `setfenv(f, env)` on a virtualized function does not change the environment
  the function body resolves globals through — the program would silently
  misbehave instead of erroring.
- `getfenv(f)` does not return the virtualized function's logical environment.
- The `debug` library (`debug.getupvalue`, `debug.setupvalue`,
  `debug.getinfo`, ...) cannot observe the simulated upvalue model correctly.

Because incorrect silent output is unacceptable, these constructs are
**rejected before transformation** with the stable error
`UNSUPPORTED_RUNTIME_INTROSPECTION` instead of being compiled and miscompiled.

## What is rejected (fail-closed, before any transformation)

1. Any **identifier token** named `setfenv`, `getfenv`, or `debug` — in any
   position (variable, local name, or field name such as `t.debug`). This is
   deliberately conservative.
2. Any **table index key inside one `[...]` pair** whose constant-folded value
   equals a forbidden name. Constant folding covers:
   - direct string keys: `_G["setfenv"]`, `t[("getfenv")]`;
   - concatenations of string literals in the same index:
     `_G["set".."fenv"]`, `t[("d").."".."ebug"]`.
   Folding is per-index and is reset by any non-literal token, so ordinary
   computed access (`t[k]`, `t[prefix .. name]`) is **not** rejected.

Strings and comments outside index keys are not scanned
(`return "setfenv debug"` stays valid input).

## What is explicitly unsupported (not always detectable)

Input that obtains `setfenv` / `getfenv` / `debug` **dynamically** is
unsupported and this scan cannot always detect it, for example:

- `local s = "setfenv"; local f = _G[s]` — value-flow across statements;
- `rawget(_G, name)` with a runtime-computed key;
- `string.char(115,101,116,102,101,110,118)` style construction;
- `loadstring("return setfenv")()`.

Such input will build successfully and the protected program may misbehave
without any diagnostic. This is a documented limitation of static analysis,
not a guarantee. Callers integrating the engine must treat the guard as a
**best-effort detector**, not a sandbox or a proof of equivalence.

## Compatibility policy

- The engine never silently changes the meaning of *supported* Lua 5.1
  constructs: anything the virtualizer cannot faithfully execute is either
  fixed at the compiler/VM level or rejected with a stable error.
- Constructs in the "unsupported" list above are rejected at policy level;
  when detected they fail the build loudly (`UNSUPPORTED_RUNTIME_INTROSPECTION`,
  worker exit code 3). When not detected, no guarantee exists.
- New introspection surfaces discovered later must be added to the `forbidden`
  table in `engine.py` together with a regression test in
  `tests/test_guard.py`.

## Regression tests

`tests/test_guard.py` covers direct identifiers (variables, fields),
computed global access (`_G["..."]`, concatenation, parenthesized keys),
and documents the accepted boundary (plain strings, dynamic aliases).
