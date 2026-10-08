# Lua 5.1 Runtime Assumptions & Luau Readiness

Status: the engine targets **Lua 5.1 only** (`engine.py` sets
`LuaVersion='Lua51'`). This document lists the runtime assumptions the
emitted code makes and what would break under Luau. **No Luau support is
claimed and no Luau runtime tests have been executed.**

## Lua 5.1 runtime assumptions in generated output

1. `getfenv` exists and `getfenv() or _ENV` yields the global environment at
   load time (`compiler/compiler.lua` container bootstrap). Luau sandboxes
   `getfenv`; Lua 5.2+ removes it.
2. `newproxy(true)` returns userdata whose `__gc`/`__len`/`__index`
   metamethods drive upvalue reference counting (`compiler/upvalue.lua`).
   The generator falls back to a table polyfill (`src/prometheus.lua`), but
   `__gc` never fires for tables on Lua 5.1 — a leak, not a correctness
   issue. Luau has `newproxy` but `__gc` semantics differ.
3. `#proxy` respects `__len` for userdata (GC-detection hack in the VM
   container). Luau supports `__len` on tables; 5.1 only on userdata.
4. `select("#", ...)` exact-arity transport tables (`Compiler:pack` /
   `arityUnpack`), `unpack`/`table.unpack` fallback, vararg `...` capture.
5. `loadstring` (5.1) rather than `load`; `setfenv`/`getfenv` are rejected in
   input by policy (`docs/INTROSPECTION_POLICY.md`).
6. Number literals: the tokenizer historically used `tonumber(s, base)`
   (32-bit `strtoul` on MSVC — fixed in Phase 2 to exact double parsing).
   The unparser historically relied on `tostring` (14-digit rounding — fixed
   in Phase 2 to verified round-trip emission).

## Unsupported Luau semantics (would require frontend + IR work)

- Type annotations, generic functions, type aliases; `continue` statement
  (parser handlers exist in `compiler/statements/continue_statement.lua` and
  `compound.lua` but are unreachable because the tokenizer/parser are locked
  to Lua 5.1).
- Compound assignment `+=` etc.; string interpolation `` `...` ``;
  generalized iteration (`for k,v in t`); `if ... then ... else ...`
  expression forms; floor-division `//` and bitwise operators.
- Luau string escapes (`\z`, `\x`, `\u{}`) and `0b` binary literals in the
  Lua 5.1 tokenizer (`BinaryNums` path exists but is only enabled for
  non-5.1 conventions).
- Roblox APIs, `task` scheduler, DataModel userdata behavior.

## Required before any Roblox/Luau claim

1. A Luau tokenizer/parser frontend emitting the same AST, with a stated
   policy for every Luau-only construct (implement or reject with a stable
   error — never silently miscompile).
2. Environment/GC/upvalue semantics resolved for Luau (`getfenv` sandbox,
   `newproxy`/`__gc` differences) — this is the open Opus problem in
   `docs/AI_HANDOFF_OPUS.md` items 1 and 3.
3. Differential tests executed on a real Luau runtime (e.g. `lune` or the
   Roblox engine), not on Lua 5.1 with Luau-ish syntax.
