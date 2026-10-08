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

## Test evidence

- `python -m unittest discover -s tests`: 23/23 OK (39.4 s) — real run this session.

## Performance results

See `evidence/benchmark_030_baseline.json` (regenerated this session).

## Known defects

- Upvalue refcount/`__gc` timing hypothesis (0.2.0 handoff problem 1) — open,
  probed by Phase 1 differential fuzzing.

## Remaining tasks

- Phase 1: differential semantic fuzzing; fix confirmed defects with regressions.
- Phase 2: RYONEX IR (typed instructions, CFG, validation, serialization).
- Phase 3: RYONEX VM v1 (independent backend; keep Prometheus backend).
- Phase 4: Luau backend compatibility layer + matrix.
- Phase 5: protection passes + BALANCED/HARDENED/MAXIMUM profiles.
- Phase 6: performance rebuild + comparison vs this baseline.
- Phase 7: security evaluation methodology.
- Phase 8: release quality (CLI, CI, docs).

## Next milestone

Phase 1 — differential semantic testing (multi-return, parens, varargs,
closures, upvalues, coroutines, tail calls, metamethods, error propagation,
environment-sensitive ops, GC).