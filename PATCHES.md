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
