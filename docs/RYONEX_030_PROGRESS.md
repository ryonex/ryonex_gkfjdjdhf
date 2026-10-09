# RYONEX OBF LAB 0.3.0 — Progress

Session branch: `cline/9tygweh4`. Environment: Linux container, Python 3.11.2,
`.venv` with `lupa==2.8` (Lua 5.1 + LuaJIT 2.1 runtimes), no system Lua.

Historical claims from 0.2.0 were NOT trusted; every number below was
re-measured this session.

## Phase 0 — Baseline (verified)

| Item | Result | Evidence |
|---|---|---|
| Unit tests (23) | ALL OK, 39.4 s | real run 2026-10-08, `python -m unittest discover -s tests` |
| Lua 5.1 correctness | 8 semantic fixtures x 2 profiles x 2 seeds pass | `tests/test_engine.py:test_semantics` |
| Arity/multi-return matrix | 34 cases pass | `tests/test_arity_matrix.py` |
| Register allocation | pressure test passes (bounded probing + first-fit fallback) | `tests/test_register_pressure.py` |
| Upvalue lifetime | GC/escape fixture passes; `__gc`-timing hypothesis still open | `tests/test_upvalue_lifetime.py` |
| Introspection guard | accepted/rejected corpus passes; dynamic aliasing documented as undetectable | `tests/test_guard.py`, `docs/INTROSPECTION_POLICY.md` |
| Prometheus integration | vendored fork, `engine.py` drives `P.Pipeline` with presets Medium/Strong; AntiTamper step stripped; integrity via `vendor-lock.json` | `engine.py`, `PATCHES.md` |
| VM behavior | Prometheus Vmify VM (arity transport tables, refcount upvalues) — covered indirectly by fixtures above | `vendor/prometheus/src/prometheus/compiler/` |
| Balanced/Hardened perf | re-measured this session | `evidence/benchmark_030_baseline.json` |

`evidence/benchmark_v2.json` is kept as the untrusted historical record.

## Completed milestones

- [x] Phase 0 baseline re-verified on this container (2026-10-08).
- [x] Phase 1 milestone 1 — differential fuzzing harness + upvalue
  correctness fix (2026-10-08):
  - `tests/difffuzz.py`: typed random-program differential fuzzer + 13 fixed
    probes covering multi-return, parens, varargs, closures, upvalues,
    coroutines, tail calls, metamethods, error propagation, globals, GC.
  - CONFIRMED + FIXED (P0): refcount+`__gc` upvalue model was unsound under
    Lua 5.1 unspecified finalizer order — user `__gc` finalizers read freed
    upvalues (crash `table index is nil` / lost writes). Pure-Lua 5.1
    counterexample reproduced the ordering; fix = GC-managed box tables
    (`compiler/upvalue.lua`, `compiler/register.lua`).
  - CONFIRMED + FIXED (profile architecture): hardened double-VM (VM-in-VM)
    pinned freed register slots until program exit (starved user finalizers,
    broke weak tables) and corrupted decoder state under GC pressure at
    specific seeds. Per policy (no redundant nesting without proven benefit)
    hardened now runs ONE Vmify + SplitStrings; the differential suite passes.
  - NON-SEMANTIC boundaries pinned by tests: error position strings, error
    identifier names, `__gc` execution order (multiset compared).
  - DEFECT (open at milestone 1, FIXED in milestone 2): proper tail calls.

## Test evidence

- 2026-10-08, final state: `python -m unittest discover -s tests` = 28/28 OK
  (~37 s), 0 expected failures. `tests/test_phase1_regressions.py` holds 10
  minimized regressions in 4 classes (finalizer upvalues, non-semantic
  boundaries, expression lists, tail calls).
- 2026-10-08, `tests/difffuzz.py` seeds 4 and 7: 43 + 53 programs+probes x
  2 profiles, 0 mismatches (evidence/difffuzz_seed4.txt, difffuzz_seed7.txt).
- 2026-10-08, milestone 1 evidence: evidence/tests_phase1.txt,
  evidence/difffuzz_phase1.txt (pre-milestone-2 state).

- 2026-10-08, `python -m unittest discover -s tests`: 27/27 OK (36.2 s),
  1 expected failure = documented tail-call defect. Includes 4 new Phase 1
  regression tests (`tests/test_phase1_regressions.py`).
- 2026-10-08, `python tests/difffuzz.py --programs 12 --seed 1`:
  25 programs+probes x 2 profiles, 2 mismatches = tail-call probe only.
- 2026-10-08, finalizer/upvalue isolation matrix (6 variants x 2 profiles):
  all match modulo `__gc` order.

## Performance results

- Baseline (pre-fix, 0.2.0 code): `evidence/benchmark_030_baseline.json` —
  balanced 11.8-56.2x, hardened 58-874x runtime vs native, double-VM 10.4x
  over single VM.
- Post-fix hardened is single-VM; its overhead is expected to approach the
  balanced range plus SplitStrings cost; re-measured in Phase 6.

## Phase 1 milestone 2 (same day): tail calls + expression lists

- CONFIRMED + FIXED (P0): proper tail calls. The VM call path did not
  trampoline `return f(...)`; recursion beyond ~15-20k nested frames raised
  `stack overflow`. Fixed with a frame-swap trampoline
  (`compiler/statements/return.lua` + a weak-keyed closure-descriptor
  registry in `compiler/compiler.lua`); verified 1,000,000-deep tail
  recursion and 30k-deep mutual tail recursion on both profiles.
- CONFIRMED + FIXED (P0): expressions beyond the target list in local
  declarations and assignments were silently dropped unless they were calls
  (`local x = 1, error("boom")` compiled as `local x = 1`). All RHS
  expressions now compile unconditionally
  (`statements/local_variable_declaration.lua`, `statements/assignment.lua`).
- Test evidence: 28/28 unit tests OK (no expected failures);
  `tests/difffuzz.py` seeds 4 and 7: 43 + 53 programs+probes x 2 profiles,
  0 mismatches. Generator bugs fixed along the way (bounded repeat, vararg
  scoping, `({})[k]` constructor).

## Known defects

- Signed-zero formatting (`0` vs `-0`) is not bit-stable through number-representation mutation (see PATCHES.md).
- Finalizer execution order differs from the original run (Lua 5.1 leaves it
  unspecified; documented non-semantic boundary).
- Error position/identifier strings differ after transformation (documented
  non-semantic boundary).

## Remaining tasks

- Phase 1 (remaining): metamethod-heavy corpus expansion; coroutines under
  stress; signed-zero stabilization.
- Phase 2: RYONEX IR (typed instructions, CFG, validation, serialization).
- Phase 3: RYONEX VM v1 (independent backend; keep Prometheus backend).
- Phase 4: Luau backend compatibility layer + matrix.
- Phase 5: protection passes + BALANCED/HARDENED/MAXIMUM profiles.
- Phase 6: performance rebuild + comparison vs this baseline.
- Phase 7: security evaluation methodology.
- Phase 8: release quality (CLI, CI, docs).

## Next milestone

Phase 2 — RYONEX IR (typed instruction representation, CFG, basic blocks,
validation, deterministic serialization, pass interface). The Prometheus
backend remains the production backend during Phase 2-3 development.

