# unprom

**A deobfuscator for the [Prometheus](https://github.com/wcrddn/Prometheus) Lua/Luau obfuscator** (originally by levno-710) — and its rebrands, notably `wearedevs.net`'s "Lua obfuscator" v1.0.0, which ships the same VM.

`unprom` parses obfuscated Lua 5.1 into an AST, runs a pipeline of per-technique reversal passes — including a **full `Vmify` lifter** that reconstructs structured Lua from the custom bytecode VM — and re-emits clean, indented source.

Pure Python 3, standard library only. CLI + importable package.

```bash
python unprom.py obfuscated.lua               # -> obfuscated.deob.lua
python unprom.py obfuscated.lua -o out.lua -v
cat obfuscated.lua | python unprom.py - > out.lua
```

or, installed:

```bash
pip install -e .
unprom obfuscated.lua -o out.lua --rename all
```

---

## What it undoes

| Prometheus step | Support | Notes |
|---|---|---|
| `WrapInFunction` | full | Peels every `return (function(...) ... end)(...)` layer. |
| `NumbersToExpressions` | full | Constant folding — arithmetic trees collapse back to literals. |
| `EncryptStrings` | full | Re-implements the keystream (`unprom/prng.py`), pulls the four embedded constants out of the prelude, turns every `S[D("…", seed)]` back into a literal, deletes the decryptor. |
| `SplitStrings` | partial | The `strcat` variant folds back to one literal; `table.concat` / custom-function variants only when the folder can see through them. |
| `ConstantArray` | good | Detects the array, replays rotation + base64 decoding statically (even when the magic numbers are themselves obfuscated), inlines every wrapper lookup, removes the setup. Bails safely if the base64 alphabet can't be read. Local wrapper *tables* not yet undone. |
| `AntiTamper` | full | Drops the top-level self-check `do … end` block (never affects output). |
| `ProxifyLocals` | partial | Best-effort for the default `LiteralType = "string"` shapes. Not in any preset. |
| `Vmify` | **lifted** (de-flatten fallback) | See [The VM](#the-vm). |
| `Watermark` | n/a | Cosmetic; left in place. |

Cosmetic passes run last: dead-store elimination, `do`-block inlining, empty/identical-`if` collapse, unused-local pruning, `local x; x = e` → `local x = e`, and identifier renaming.

---

## The VM

`Vmify` compiles the whole script to a **block-based bytecode** with its own register file, upvalue machinery and closure factories, dispatched by a `while pos do … end` loop whose body is a *balanced binary search tree* of `if pos < K then … else … end` over **shuffled** block ids.

`--devirt` picks how far to go:

### `--devirt lift` (default) — reconstruct structured Lua

1. flatten the search tree into an ordered block list;
2. **symbolically execute** each block over the register file — `rT="foo"; rX=ENV[rT]` → `foo`, `rX = a + b` → an expression, `rTmp={f(x)}; rA=rTmp[1]` → a call, `createClosureN(id,{…})` → a nested `function … end`, `allocUpval()`/`upvals[k]` → a captured `local`, scope-cleanup `reg = nil` noise dropped;
3. classify each block's terminator from the symbolic value of `pos` / `ret` (goto / branch / return); lift cross-block registers to real `local`s;
4. **structure** the CFG with dominator / post-dominator analysis into `if` / `elseif` / `else`, `while`, `continue`. Functions that don't structure cleanly fall back to the de-flattener.

A `count(n)` helper round-tripped through Vmify and back:

```lua
(function(a1)
  local c1 = 0
  while c1 < a1 do
    c1 = c1 + 1
  end
  return c1
end)(3)
```

### `--devirt deflatten` — just un-shuffle the dispatcher

Reconstruct the block list in id order, map opaque ids (`0 … 2^24`) to `0, 1, 2, …`, rewrite every `pos = <id>` transition, and replace the search tree with a flat `if pos == 0 then … elseif …` chain. Same VM, linear dispatch, real block numbers — a starting point for manual analysis when the full lift falls short.

### `--devirt off` — leave the VM untouched

### Honest status of the lifter

Validated against hand-compiled VM fixtures **and** one real ~76-block script (`ConstantArray(base64) + EncryptStrings + Vmify`, a wearedevs.net-obfuscated Roblox loader). On that sample the lifter recovers global/library resolution (`string.gmatch`, `math.random`, `pcall`, `setmetatable`, …), `while` / `if` / `else` / `continue` structure, the AntiTamper self-check loop, all nested VM closures (including the `EncryptStrings` keystream + decryptor with their constants visible), and the payload (`_G[...] = ...`, `queue_on_teleport`, `task.spawn`).

Known weak spots, roughly by noise produced:

- **Upvalue-table leakage** — when a VM register name collides with a helper name, cell recognition misses and you get raw `t[slot] = v` / `p[k]` indexing instead of a captured `local`.
- Numeric `for` / `for-in` render as `while true do i = i + step; if <bounds> then <body> continue end break end` — correct, not sugared.
- A second `EncryptStrings` layer *inside* the VM is lifted structurally but its `decrypt("<cipher>", <seed>)` calls aren't evaluated (the constants are visible in the lifted decryptor, so this is doable next).
- `repeat`, deep upvalue chains, some multi-assignment.
- Register locals keep single-letter names unless `--rename all`.

---

## Usage

```
unprom INPUT [-o OUTPUT] [options]

  INPUT                  obfuscated .lua file, or - for stdin
  -o, --output           output path (default <input>.deob.lua; - for stdout)
  --passes LIST          explicit comma-separated pass list
  --skip LIST            drop passes from the default pipeline
  --rename {none,auto,all}
                         auto (machine-looking names only, default),
                         all (every local -> v1, v2 …), none
  --devirt {lift,deflatten,off}
                         Vmify handling (default: lift)
  --no-devirt            alias for --devirt off
  --indent STR           indentation (default: two spaces)
  -v, --verbose          per-pass notes and statistics
  --list-passes          show pass names and default order
```

### Library

```python
from unprom import deobfuscate
clean = deobfuscate(open("obf.lua").read())

# finer control
from unprom import Pipeline
text, ctx = Pipeline(passes=["unwrap", "encrypt_strings", "constant_fold"],
                     options={"rename": "all"}).run(src)
print(ctx.notes, ctx.stats)
```

### Default pipeline

```
unwrap → anti_tamper → constant_fold → encrypt_strings → constant_array
       → constant_fold → vm_lift → devirtualize → constant_fold
       → proxify_locals → constant_fold → dead_code → cleanup → rename
```

A pass that throws is logged in `ctx.notes` and skipped — it never aborts the run.

---

## Project layout

```
unprom.py                  dev shim so `python unprom.py` works from a clone
unprom/
  cli.py                   argument parsing + I/O
  lexer.py                 Lua 5.1/5.2 tokenizer (+ a few Luau tolerances)
  parser.py                recursive-descent parser -> AST
  ast_nodes.py             AST dataclasses + visitor / transformer / clone
  unparser.py              AST -> formatted Lua
  scope.py                 lexical binding resolver
  evaluator.py             constant evaluation of pure expressions
  prng.py                  EncryptStrings keystream (port of the Lua)
  astutil.py               small pattern-matching helpers
  vm.py                    Vmify container discovery + block flattening
  pipeline.py              pass registry + runner
  passes/
    wrap_in_function.py  anti_tamper.py  constant_fold.py  encrypt_strings.py
    constant_array.py  proxify_locals.py  dead_code.py  cleanup.py  rename.py
    devirtualize.py       VM dispatch de-flattening
    vm_lift.py            full VM -> structured Lua
tests/
  roundtrip.py             parse -> unparse fixed-point over corpus/
  test_passes.py           per-pass unit checks
  test_vm_lift.py          VM lifter against hand-compiled + real fixtures
  test_e2e.py              layered obfuscation -> full pipeline
  run_all.py               runs everything
corpus/                    hand-written Lua exercising the grammar
samples/                   before / after examples
```

### Tests

```bash
python tests/run_all.py
```

Standard library only — no test dependencies. The suite covers a parse⇄unparse
fixed-point over `corpus/`, each pass in isolation, the VM lifter against
hand-compiled `Vmify` fixtures, and a full end-to-end run over a layered
`wrap ∘ encrypt ∘ number-expr` obfuscation.

To test against a real obfuscated sample, drop it at
`tests/fixtures/real_wearedevs_mm2.lua` (git-ignored — see
`tests/fixtures/README.md`); the corresponding test activates automatically.

---

## Scope & honesty

- The parser targets **Lua 5.1** (what Prometheus emits). `goto`/labels, bitwise ops and a few Luau conveniences are accepted; full Luau type syntax is only skipped heuristically.
- `EncryptStrings` recovery reproduces the keystream arithmetic in IEEE doubles exactly as the Lua does, and is validated for self-consistency (`encrypt`∘`decrypt` round-trips for random keys/seeds).
- `ConstantArray` assumes the standard global-wrapper layout; heavily tuned configs may be only partially resolved — `-v` reports what was and wasn't handled.
- `Vmify` lifting is best-effort. It has been checked on hand-compiled fixtures and one real sample; expect rough edges on other configs, and a de-flatten fallback when structuring fails.

This is a reverse-engineering / analysis tool. Use it on code you have a right to analyze.

---

## License

MIT — see [LICENSE](LICENSE). Prometheus itself is AGPL-3.0; no Prometheus source is vendored here.
