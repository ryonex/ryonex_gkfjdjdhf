# RyoNex 0.3.0 — core engine evolution

## Upvalue model: refcount + `__gc` replaced by GC-managed boxes (P0 fix)

- `prometheus/compiler/upvalue.lua`, `prometheus/compiler/register.lua`:
  shared variables now live in one-field "box" tables (`box[1]`); registers
  and closure entry lists reference the box directly. Lifetime is plain Lua
  reference semantics — no ids, no reference counts, no `__gc`-driven frees.
- Motivation (confirmed defect, reproducible in pure Lua 5.1): the old model
  freed upvalue ids from the entries-proxy `__gc`, but Lua 5.1 runs finalizers
  in unspecified order, so a userdata finalizer could read its own closure
  upvalues AFTER the proxy had already decremented/freed them — silent value
  loss (`table index is nil`) or lost writes. Minimal counterexample and
  evidence: docs/RYONEX_030_PROGRESS.md, tests/test_phase1_regressions.py.
- The entries-proxy keeps a no-op `__gc` so it stays in the GC's preserved
  (finalizable) set while user finalizers run; its metatable (`__index` =
  entry list) then keeps the captured boxes reachable regardless of
  finalization order.

## Hardened profile: single virtualization (profile architecture fix)

- `engine.py`: the hardened build drops the Strong preset's double Vmify
  (VM-in-VM) and gains `SplitStrings` instead. Double nesting measured 10.4x
  runtime over single VM (`evidence/benchmark_030_baseline.json`) with no
  evaluated resistance benefit, and carried two confirmed defects that only
  manifest under the outer VM layer: (1) freed register slots pin their last
  value until program exit, starving user `__gc` finalizers and breaking weak
  tables; (2) decoder-state corruption under GC pressure produced wrong
  string bytes at specific seeds. Both classes are eliminated by the single
  VM profile; the differential suite passes on balanced and hardened.
  Per project policy, VM nesting returns only with proven benefit (Phase 5
  MAXIMUM evaluation).

## Semantic boundaries (documented, non-semantic by Lua reference)

- Error POSITION strings (`chunk:line:`) embedded in `error(msg)`/`assert`
  messages cannot survive source transformation; differential testing
  normalizes them (tests/difffuzz.py).
- Error messages embedding identifier names (`attempt to call local 'x'`,
  `bad argument #1 to 'f'`) change under renaming; normalized likewise.
- `__gc` finalizer EXECUTION ORDER is unspecified in Lua 5.1; only the
  multiset of finalizer effects is compared (tests/test_phase1_regressions.py).

## Known defects (tracked, regression-tested)

- Proper tail calls: the VM call path does not trampoline tail calls; tail
  recursion beyond the host Lua stack limit (~15-20k nested frames) raises
  `stack overflow` where Lua 5.1 runs unbounded. Regression test marked
  expectedFailure: tests/test_phase1_regressions.py.

# RyoNex 0.2.0 — local fork changes

Based on Prometheus by Elias Oelschner, https://github.com/prometheus-lua/Prometheus

Upstream base: a4efc5f381c50ae2a111bdbb9272fa3203685be3.
This package contains a modified fork, not unchanged upstream. Exact diff: evidence/prometheus.patch.

## Phase 2 verified core repair (Windows-native session)

- `prometheus/unparser.lua`: number literals now round-trip exactly. Lua 5.1
  `tostring` rounds to 14 significant digits and silently corrupted literals
  such as `123456789012345678` in every profile; the unparser verifies the
  round trip and falls back to `%.17g`.
- `prometheus/steps/NumbersToExpressions.lua` (NumberRepresentationMutation,
  hardened profile only): every mutated representation (hex/binary/scientific)
  is verified to re-parse to the exact original value before emission. Two
  confirmed corruption bugs fixed: `string.format("0x%X", val)` truncates
  integers >= 2^32 on platforms with 32-bit `long` (MSVC), which broke every
  hardened build on Windows (the EncryptStrings constants are >= 2^32); and
  the `%.15g` scientific form lost precision beyond 15 significant digits.
- `prometheus/tokenizer.lua`: hex/binary integer literals are parsed with
  exact double arithmetic instead of `tonumber(s, base)` (32-bit `strtoul`
  wrap on MSVC); values above 2^53 defer to the runtime parser.
- `prometheus/compiler/register.lua`: register allocation uses bounded random
  probing (<= MAX_REGS attempts) with a deterministic first-fit fallback that
  also serves the spill-table region; the unreachable `MAX_REGS_MUL` branch
  and constant (`compiler/constants.lua`) are removed.
- `engine.py`: the introspection guard now rejects constant-string index keys
  (including literal concatenations and parenthesized keys) naming
  `setfenv`/`getfenv`/`debug`, not only direct identifiers. Policy and the
  documented dynamic-aliasing boundary: docs/INTROSPECTION_POLICY.md.

Note on historical evidence: the 0.2.0 Linux-container test run did not
manifest the `%X`/`strtoul` truncation (64-bit `long`) and its fixtures did
not cover the >14-digit rounding bug; both were found and fixed during the
Windows-native Phase 2 verification.

## Correctness fixes

- Parenthesized function calls and varargs produce one value in argument lists, return lists and table constructors. Assignment destinations beyond the first are padded with nil.
- VM transport tables now carry explicit arity. Calls, returns, vararg capture and entry arguments preserve trailing nil. The VM's internal unpack respects that arity. Ordinary user tables retain their original construction.
- Direct identifier references to setfenv/getfenv/debug are rejected before transformation because virtualization does not preserve their introspection semantics. This is a compatibility guard, not a sandbox or complete detector: dynamically resolved or aliased APIs can evade detection and are unsupported too. Identifiers with those names used as locals/fields are conservatively rejected; strings/comments are not.

## Build reliability

- Bounded reads and regular-file requirements for inputs and worker artifacts.
- Validate options and reject bools used as integer seeds/timeouts.
- Verify vendored engine hashes before builds and in doctor. This detects accidental drift against the local manifest; it is not a signed supply-chain attestation.
- Stage complete output beside destination, fsync, and publish with an atomic hard link, preserving existing files and concurrent-writer safety. Requires a filesystem supporting hard links; unsupported filesystems fail closed.
- Reject output symlinks. Inherited worker environment is restricted to runtime essentials.
- Stable errors for unsupported introspection and invalid UTF-8; no source fragments in CLI errors.

## Remaining limits

No proof of universal semantic equivalence, resistance to all deobfuscators, or superiority over Luraph. LuaU/Roblox integration, hostile multi-user isolation and production web integration remain unfinished. Testing was performed in this Linux container; Windows CLI/filesystem behavior still needs native verification.
