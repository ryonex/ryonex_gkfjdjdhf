# AI_TASKS_CHEAP_MODEL.md — RyoNex OBF Lab 0.2.0

Routine tasks implementable without expensive reasoning. Do **not** start the
items marked "BLOCKED" until `docs/AI_HANDOFF_OPUS.md` records its decision.
No production source may be modified for BLOCKED items. Historical evidence in
`evidence/` must not be presented as newly verified — regenerate files and
record commands/date when re-running.

## P2 — Guard hardening (routine part only)

- Task: extend the lexical introspection guard in `engine.py` (`transform`,
  the token scan) to also catch direct `_G["setfenv"]`-style constant-string
  index patterns and `_G` / `rawget` / `rawset` / `loadstring` / `load`
  identifier references, consistent with the existing policy. Keep strings in
  comments/literals allowed.
- Files: `engine.py`; add cases to `tests/test_boundaries.py`.
- Note: dynamic aliasing stays undetectable — document, don't over-claim.
  The *semantic* policy (support vs. reject) is Opus problem 3; this task only
  widens the existing lexical net.

## P2 — Deobfuscation-probe automation and breadth

- Task: turn the 6-probe matrix described in `evidence/deob_probe_v2.json`
  into a repeatable script under `tests/` (protect → unprom → try to run
  deob output under Lua 5.1 with instruction budget → record JSON). Extend
  fixtures from the single "sum" fixture to all `tests/test_engine.py` CASES
  and `tests/stress.py` EXTRA cases.
- Files: new `tests/deob_probe.py`; regenerate `evidence/deob_probe_v3.json`
  with command, date, unprom version.
- Note: root-cause interpretation of failures is Opus problem 6 — record
  results only, no resistance claims.

## P2 — Benchmark breadth

- Task: `tests/benchmark.py` measures one microbenchmark. Add workload
  classes: string-heavy, closure-heavy, table-heavy, call-heavy, mixed; keep
  median-of-9 methodology; record size and runtime ratios per profile.
- Files: `tests/benchmark.py`; regenerate `evidence/benchmark.json` with date.

## P3 — Native Windows verification

- Task: `PATCHES.md` states testing happened in a Linux container. Run
  `python -m unittest discover -s tests -v`, `tests/stress.py`,
  `tests/benchmark.py` natively on Windows (this machine), record results and
  any hard-link / symlink / path-length differences in a new
  `evidence/platform_windows.txt`.
- Files: new evidence file only.

## P3 — Test gaps in engine.py

- Task: add unit tests for: `doctor` command (ready + not-ready paths),
  `worker` exit-code mapping (3/4/2), `publish` on a filesystem without
  hard-link support (fail-closed behavior), `bounded_read` non-regular-file
  rejection, seed bounds 1 and 2147483647 (valid edges).
- Files: `tests/test_boundaries.py` / new `tests/test_cli.py`.

## P3 — Vendor integrity scope

- Task: `engine.py` `verify_vendor` checks only `vendor/prometheus/` entries.
  `research-tools/unprom/` is unverified. Either add unprom files to
  `vendor-lock.json` or document in `README_TH.md` why research tools are
  excluded (they never run during builds).
- Files: `vendor-lock.json` or `README_TH.md`; add a test asserting the
  chosen invariant.

## P3 — Luau frontend groundwork (non-semantic part)

- Task: add *failing-marker* (expected-fail) tests capturing current behavior
  for Luau syntax (type annotations, `continue`, compound assignment,
  string interpolation, generalized iteration) through `engine.py protect`,
  so later frontend work has a red/green baseline. Do not implement the
  parser changes. Do not strip syntax with regex.
- Files: new `tests/test_luau_gaps.py`.
- Note: environment/GC/upvalue semantics on Luau are Opus problems 1 and 3 —
  BLOCKED until decided.

## P3 — Attribution of generated output

- Task: check whether any preset emits the Prometheus credit (upstream
  `Watermark`/`WatermarkCheck` steps exist in
  `vendor/prometheus/src/prometheus/steps/` but are not in either profile).
  Per license notes in `README_TH.md`, ensure the credit appears in the docs
  and evaluate adding a non-semantic attribution comment to output; record
  the decision either way.
- Files: `README_TH.md`; optionally a profile change behind a flag.

## P3 — Documentation accuracy

- Task: `README_TH.md` "five goals" and limitations sections are accurate but
  the unprom probe line ("ผลที่แกะไม่ผ่านการรันเทียบต้นฉบับ") should link the
  methodology caveat from `evidence/deob_probe_v2.json`'s own warning
  (single fixture, unprom limitations may explain failures). Mirror that
  caveat wherever probe results are mentioned.
- Files: `README_TH.md`, `PATCHES.md`.

## P3 — Register allocator mechanical fix — BLOCKED

- Task: after Opus problem 5 decides the allocator model, implement the
  bounded/deterministic allocation and remove the dead
  `MAX_REGS_MUL` branch. Until then, do not patch
  `vendor/prometheus/src/prometheus/compiler/register.lua`.

## P3 — Hardened profile rework — BLOCKED

- Task: any change to the double-Vmify `hardened` pipeline is blocked on
  Opus problem 4 (keep/drop/redesign decision with cost model).
