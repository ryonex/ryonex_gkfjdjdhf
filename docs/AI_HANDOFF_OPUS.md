# AI_HANDOFF_OPUS.md — RyoNex OBF Lab 0.2.0

## PHASE 2 STATUS (this session)

P0 discovered and FIXED (do not re-escalate; recorded in PATCHES.md):
1. `steps/NumbersToExpressions.lua` `NumberRepresentationMutation` silently
   corrupted numbers (hardened profile): `%X` truncates >= 2^32 on 32-bit
   `long`; `%.15g` loses precision beyond 15 digits. Every hardened build of
   every fixture failed on Windows before the fix (verified red -> green).
2. `unparser.lua` `tostring` (14-digit) corrupted literals > 14 significant
   digits in ALL profiles (`return 123456789012345678` returned
   `123456789012350000`). Fixed with verified round-trip emission.
3. `tokenizer.lua` `tonumber(s, base)` (32-bit `strtoul`) wrapped hex/binary
   literals >= 2^32 (`0x1FFFFFFFF` became `4294967295`). Fixed with exact
   double parsing.

Item status: (1) upvalue lifetime — GC/coroutine/escape tests added
(`tests/test_upvalue_lifetime.py`), no reproducible defect; GC-timing
hypothesis below remains open for exotic finalizer paths. (2) arity — full
matrix added (`tests/test_arity_matrix.py`, 34 cases), no confirmed
remaining mismatch. (5) allocator — resolved: bounded probing + deterministic
first-fit fallback (`register.lua`), stress tests pass. Guard (item 3 static
part) strengthened + documented (`docs/INTROSPECTION_POLICY.md`,
`tests/test_guard.py`). Item 4 now has measured cost: double VM = 12.3x over
single VM on the same workload; hardened 63-961x runtime on 5 workloads
(`evidence/benchmark_v2.json`). Items 1-GC, 3-IR, 4-architecture and 6
remain open for expensive reasoning.

---

Problems below justify expensive reasoning (complex VM semantics, compiler IR,
closure/upvalue correctness, hard Lua/Luau incompatibilities, fundamental
protection architecture). Routine work is in `docs/AI_TASKS_CHEAP_MODEL.md`.
Evidence marked **confirmed** was read from source this session; **hypothesis**
items are clearly labeled. Historical results in `evidence/` were **not**
re-verified.

## 1. Upvalue lifetime machinery: refcount + `newproxy(true)`/`__gc` simulation

- File: `vendor/prometheus/src/prometheus/compiler/upvalue.lua`
  (`Compiler:createUpvaluesGcFunc`, `Compiler:createFreeUpvalueFunc`,
  `Compiler:createAllocUpvalFunction`, `Compiler:createUpvaluesProxyFunc`);
  `vendor/prometheus/src/prometheus/compiler/compile_core.lua`
  (`Compiler:compileBlock` end-of-block `freeUpvalueFunc` calls);
  `vendor/prometheus/src/prometheus/compiler/compile_top.lua`
  (`Compiler:compileFunction`).
- Evidence: **confirmed** — upvalues are simulated as entries in one runtime
  table keyed by a monotonic id with manual reference counting; lifetime is
  tracked via `newproxy(true)` userdata whose `__gc` decrements counts, and VM
  cleanup is *detected* via `#detectGcCollectVar` (`__len` on a proxy).
- Expected: closures capturing locals behave exactly like native Lua upvalues
  regardless of GC timing or `collectgarbage()` calls inside customer code.
- Actual: no test exercises GC interaction (`tests/stress.py` never calls
  `collectgarbage`). Whether scope-exit refcount decrements can free an id a
  still-live closure later reads is **hypothesis** — unproven either way.
- Minimal reproduction: fixture creating many closures in a loop, storing a
  subset, forcing `collectgarbage("collect")` between allocations, then
  invoking the stored closures; differential under Lua 5.1 + LuaJIT 2.1 with
  instruction budget.
- Architecture question for Opus: is a table+refcount+`__gc` upvalue model
  provably correct for Lua 5.1 semantics (incl. `__gc` error behavior
  differences in LuaJIT), or must the IR move to real Lua closures/native
  upvalues? What are the exact lifetime invariants?
- Acceptance criteria: written invariant proof or a red/green differential
  corpus (≥50 adversarial closure-lifetime fixtures) passing on Lua 5.1 +
  LuaJIT; a recorded keep/replace decision for the proxy/GC mechanism.

## 2. Multi-value (arity) semantics of the VM IR after the RyoNex patches

- Files: `vendor/prometheus/src/prometheus/compiler/compile_core.lua`
  (`Compiler:compileExpression` — the "RyoNex fix" for parenthesized
  expressions), `vendor/prometheus/src/prometheus/compiler/compiler.lua`
  (`Compiler:pack`, `Compiler:arityUnpack`),
  `vendor/prometheus/src/prometheus/compiler/emit.lua`
  (`canMergeParallelAssignmentStatements`, merged-statement emission).
- Evidence: **confirmed** — arity is patched ad hoc: parenthesized expressions
  forced to 1 value with nil padding; VM transport tables
  `{n = select("#", ...), ...}` with an arity-aware unpacker
  (`t[i or 1], 1, j or t.n or #t`); `emit.lua` merges assignments under
  hand-written read/write-set rules.
- Expected: every Lua 5.1 adjustment context preserves exact value counts:
  last-expression expansion in calls/returns/table constructors, truncation
  when not last, `t[f()]` index targets, method/self calls, vararg capture,
  `local a,b = f()`, `return;` vs `return nil;`, plus interaction with the
  statement merger.
- Actual: historical bugs were fixed (`PATCHES.md`; `tests/stress.py`
  `paren_*`, `trailing_nil`), but no systematic enumeration exists; residual
  gaps are **hypothesis**.
- Minimal reproduction: extend `tests/stress.py` `EXTRA` with a matrix of all
  Lua 5.1 adjustment positions; any mismatch on balanced/hardened across
  seeds is a confirmed defect.
- Architecture question for Opus: state the complete multi-value contract the
  register-machine IR must satisfy, enumerate every producer/consumer site,
  and find any context the paren/transport patches still get wrong (incl.

## 3. Environment model and the bypassable introspection guard (P0/P1)

- Files: `engine.py` (`transform`, lexical token scan rejecting
  `setfenv`/`getfenv`/`debug` identifiers),
  `vendor/prometheus/src/prometheus/compiler/compiler.lua` (container
  bootstrap env: `getfenv and getfenv() or _ENV`),
  `tests/test_boundaries.py` (`test_environment_introspection_rejected`,
  `test_introspection_words_in_strings_are_allowed`).
- Evidence: **confirmed** — the guard is a token-identifier scan; strings are
  deliberately allowed, so `_G["setfenv"]` or `_G["set"+"fenv"]` passes it.
  `README_TH.md`/`PATCHES.md` admit the guard is conservative, not a detector.
- Expected: guarded constructs are rejected; anything that slips through
  either works identically or fails loudly.
- Actual: bypassable input is silently accepted and then **misbehaves under
  the VM** (globals resolve through the VM's captured env, not the caller's
  function environment) — incorrect program behavior with no diagnostic.
  **Hypothesis** on the exact misbehavior; the bypass itself is confirmed.
- Minimal reproduction: rewrite the existing guard test as
  `local g=_G;local sf=g["set"+"fenv"];...` — it passes the guard; compare
  original vs. protected output on Lua 5.1.
- Architecture question for Opus: define the precise environment-model
  semantics virtualized code may assume (5.1 globals via `getfenv() or _ENV`,
  5.2+ `_ENV`, LuaJIT, Luau sandboxed `getfenv`); decide whether the VM must
  *support* or *reject-with-certainty* environment introspection, and what a
  sound (non-lexical) rejection strategy looks like — or whether IR-level
  support is feasible.
- Acceptance criteria: written environment-semantics policy; either a sound
  rejection strategy with tests or VM-level support with differential tests;
  no known silent-misbehavior path.

## 4. Hardened profile = double Vmify: fundamental protection-architecture decision

- Files: `engine.py` (`transform`, preset map `'hardened' -> 'Strong'`);
  `README_TH.md` profile table (Vmify → EncryptStrings → Vmify);
  `evidence/benchmark.json` (historical: 144.8x runtime, 26,168 B vs 49 B).
- Evidence: code path **confirmed**; performance numbers are **historical
  evidence, not newly verified**.
- Expected: a hardened profile whose added layers buy measurable resistance.
- Actual: the same VM (same lifter attack surface) is applied twice;
  `research-tools/unprom` ships a full Vmify lifter for upstream shapes.
  Cost is extreme (approx. 145x runtime, approx. 534x size on the
  microbenchmark) and nesting compounds the upvalue/GC machinery from
  problem 1. Whether nesting adds real resistance is **hypothesis** — never
  measured.
- Minimal reproduction: none needed for the decision; resistance can only be
  measured after problem 6's methodology exists.
- Architecture question for Opus: given a register-machine VM whose dispatch
  (`while pos` + shuffled BST of `if pos < K`) and closure factories are
  publicly liftable, which layers actually raise lifter cost — per-seed IR
  variation, structurally different second-stage VM, opaque predicates,
  closure-model variation — versus mechanical nesting? Recommend
  keep/drop/redesign for `hardened` with cost bounds.
- Acceptance criteria: a written recommendation with a cost model

## 5. Register allocator: random probing with a dead fallback path

- Files: `vendor/prometheus/src/prometheus/compiler/register.lua`
  (`Compiler:allocRegister`),
  `vendor/prometheus/src/prometheus/compiler/constants.lua`
  (`MAX_REGS = 100`, `MAX_REGS_MUL = 0`),
  `vendor/prometheus/src/prometheus/compiler/emit.lua` (table-register
  fallback when `maxUsedRegister >= MAX_REGS`).
- Evidence: **confirmed** — the sequential fallback branch
  `if self.usedRegisters < MAX_REGS * MAX_REGS_MUL` evaluates to
  `usedRegisters < 0`, i.e. dead code. Allocation is
  `repeat id = math.random(1, MAX_REGS-1) until not registers[id]` —
  O(load) probing that relies on the table-register fallback engaging first
  near exhaustion.
- Expected: guaranteed termination under any register pressure; predictable
  compile time.
- Actual: whether extreme nesting/arity in customer code can stall allocation
  before the fallback engages is **hypothesis**; the dead branch and the
  quadratic probing are confirmed.
- Minimal reproduction: generate a fixture with very high simultaneous
  register pressure (deeply nested expressions/calls) and attempt a build
  under a timeout; record allocator behavior.
- Architecture question for Opus: is the fixed-100 register file with a single
  table-register escape hatch the right IR model (vs. unlimited virtual
  registers lowered to locals), and what invariant guarantees termination?
  Should allocation be deterministic given the seed?
- Acceptance criteria: termination proof or bounded deterministic allocator;
  stress builds complete without livelock; decision recorded.

## 6. Deobfuscation-probe failures: resistance evidence or tool mismatch?

- Files: `evidence/deob_probe_v2.json` (all 6 probes
  `semantic_recovery: false`, `LuaError`/`LuaSyntaxError`, on a single trivial
  sum fixture), `research-tools/unprom/README.md` (claims full Vmify lift for
  upstream shapes), `research-tools/unprom/unprom/vm.py` and
  `research-tools/unprom/unprom/passes/vm_lift.py` (symbol-pattern container
  discovery), `vendor/prometheus/src/prometheus/compiler/compiler.lua`
  (`pack`/`arityUnpack` — the fork's deviations from the upstream VM shape).
- Evidence: probe file contents **confirmed**; **hypothesis** that the arity
  transport tables and patched emission break unprom's pattern matching
  rather than the VM resisting lifting.
- Expected: resistance claims backed by demonstrated recovery failure with
  root cause.
- Actual: unprom fails to produce runnable output, but the fork deviates from
  the upstream VM shapes unprom was built against; failure proves nothing
  about a lifter updated for the fork. Single trivial fixture; one tool.
- Minimal reproduction: run unprom on an upstream-Prometheus build of the same
  fixture vs. a RyoNex build; diff where the lift diverges (`-v` notes).
- Architecture question for Opus: which fork deviations (if any) genuinely
  raise lifting cost vs. merely being unfamiliar to this particular tool? Is
  accidental pattern-breaking a defensible protection strategy, or must
  protection be designed against an *adaptive* lifter?
- Acceptance criteria: written root-cause analysis of the unprom failures and
  a non-circular resistance-evaluation methodology (>= 2 tools, >= 3 fixture
  classes, adaptive-lifter assumption documented).

## Cross-cutting note for Opus

Lua 5.1 vs Luau gaps: the tokenizer/pipeline hardcode `LuaVersion='Lua51'`
(`engine.py` lines 62 and 72); `compiler/statements/compound.lua` and
`continue_statement.lua` exist but are unreachable; the emitted VM assumes
`getfenv`, `newproxy`, `__gc` and `select("#", ...)`, none verified on Luau.
Frontend work (parsing Luau, enabling existing statement handlers) is routine
and lives in the cheap-model doc; the *environment/GC/upvalue semantics on
Luau* must be resolved together with problems 1 and 3 above before any
Roblox claim.

  (runtime/size budgets per profile) tied to lifter mechanics; `hardened`
  redesigned, dropped, or explicitly justified.

  whether `arityUnpack`'s `t.n or #t` can observe a user table with an `n`
  key).
- Acceptance criteria: written arity contract + full adjustment-context
  differential corpus passing 2 profiles × 3 seeds × 2 runtimes; defects fixed
  at the IR level, not per-site.
