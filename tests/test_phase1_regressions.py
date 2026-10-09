"""Phase 1 (0.3.0) regression tests for confirmed semantic defects.

Every confirmed defect found by tests/difffuzz.py gets a minimized regression
here. Non-semantic boundaries (error position strings, __gc execution order,
renamed identifiers in error text) are asserted modulo their documented
normalization.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

PROFILES = ("balanced", "hardened")


def run(source):
    lua = LuaRuntime(unpack_returned_tuples=True)
    fn = lua.eval("function(...) " + source + " end")
    return fn()


def build_and_run(source, profile, seed=1):
    with tempfile.TemporaryDirectory() as d:
        inp, out = Path(d) / "in.lua", Path(d) / "out.lua"
        inp.write_text(source)
        protect(inp, out, profile, seed)
        return run(out.read_text())


class FinalizerUpvalueRegression(unittest.TestCase):
    """0.3.0 defect: closures reachable only via userdata __gc finalizers
    read freed upvalues under the old refcount+__gc model (Lua 5.1 runs
    finalizers in unspecified order; the entries-proxy __gc could decrement
    before the user finalizer read). Fixed by the box-based upvalue model."""

    CASES = {
        "loopvar_capture": (
            "local fired = {}\n"
            "for i = 1, 5 do\n"
            "    local p = newproxy(true)\n"
            "    getmetatable(p).__gc = function() fired[i] = true end\n"
            "    p = nil\n"
            "end\n"
            "collectgarbage('collect'); collectgarbage('collect')\n"
            "local n = 0\n"
            "for i = 1, 5 do if fired[i] then n = n + 1 end end\n"
            "return n"
        ),
        "param_capture": (
            "local fired = {}\n"
            "local function mk(i)\n"
            "    local p = newproxy(true)\n"
            "    getmetatable(p).__gc = function() fired[i] = true end\n"
            "    p = nil\n"
            "end\n"
            "mk(1); mk(2); mk(3)\n"
            "collectgarbage('collect'); collectgarbage('collect')\n"
            "return fired[1], fired[2], fired[3]"
        ),
        "sibling_state_sharing": (
            "local E, O, U = 5, 2, {}\n"
            "local function V()\n"
            "    if #U == 0 then\n"
            "        E = ((E * 213 + 27552854471197)) % 35184372088832\n"
            "        O = (O * 201) % 257\n"
            "        U = {E % 256, O % 256}\n"
            "    end\n"
            "    local r = #U\n"
            "    local k = U[r]\n"
            "    U[r] = nil\n"
            "    return k\n"
            "end\n"
            "local function decode(n, seed)\n"
            "    U = {}; E = seed % 35184372088832; O = seed % 255 + 2\n"
            "    local b, p = 204, {}\n"
            "    for O = 1, n do\n"
            "        b = (1 + V() + b) % 256\n"
            "        p[#p + 1] = b\n"
            "    end\n"
            "    return unpack(p)\n"
            "end\n"
            "return decode(7, 22126428612671)"
        ),
    }

    def test_finalizer_upvalue_integrity(self):
        for name, source in self.CASES.items():
            expected = run(source)
            for profile in PROFILES:
                with self.subTest(case=name, profile=profile):
                    self.assertEqual(expected, build_and_run(source, profile))


class NonSemanticBoundaries(unittest.TestCase):
    """Documented non-semantic differences; these assertions pin the
    contract so accidental semantic drift is still caught."""

    def test_finalizer_order_unspecified(self):
        # Lua 5.1 leaves __gc execution order unspecified; only the multiset
        # of finalizer effects is semantic.
        source = (
            "local out = {}\n"
            "for k = 1, 4 do\n"
            "    local i = k\n"
            "    local p = newproxy(true)\n"
            "    getmetatable(p).__gc = function() out[#out + 1] = i end\n"
            "    p = nil\n"
            "end\n"
            "collectgarbage('collect'); collectgarbage('collect')\n"
            "table.sort(out)\n"
            "return table.concat(out, ',')"
        )
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))

    def test_error_position_normalized(self):
        # error(msg) with default level embeds source positions that cannot
        # survive transformation; the message text must match modulo the
        # position prefix.
        source = (
            "local ok, e = pcall(function() error('boom') end)\n"
            "return ok, (e:gsub('^.-:%d+: ', ''))"
        )
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))


class ExpressionListRegression(unittest.TestCase):
    """0.3.0 defect (FIXED): expressions beyond the target list in local
    declarations and assignments were silently dropped unless they were
    calls. Lua 5.1 evaluates the full list left to right."""

    CANON = (":gsub([=[^.-:%d+: ]=], [[]])"
             ":gsub([=[.*%((.*)%)$]=], [[(%1)]])")
    CASES = {
        "extra_side_effect": (
            "local ok, e = pcall(function() local x = 1, "
            "error(\"must-run\", 0) return x end) return ok, e"
        ),
        "extra_index_error": (
            "local ok, e = pcall(function() "
            "local a = 1, NIL_GLOBAL_T[1] return a end) "
            "return ok, (e" + CANON + ")"
        ),
        "assign_extra": (
            "local ok, e = pcall(function() local a = 1, 2, "
            "error(\"must-run\", 0) return a end) return ok, e"
        ),
        "assign_extra_index": (
            "local ok, e = pcall(function() local a, b = 1, 2, "
            "NIL_GLOBAL_T[1] return a, b end) "
            "return ok, (e" + CANON + ")"
        ),
    }

    def test_extra_expressions_evaluated(self):
        for name, source in self.CASES.items():
            expected = run(source)
            for profile in PROFILES:
                with self.subTest(case=name, profile=profile):
                    self.assertEqual(expected, build_and_run(source, profile))


class TailCallRegression(unittest.TestCase):
    def test_deep_tail_call_is_proper(self):
        # 0.3.0 defect (FIXED): the VM call path did not trampoline tail
        # calls; recursion depth beyond the Lua stack (~15-20k nested
        # container frames) raised 'stack overflow'. Fixed by the frame-swap
        # trampoline in compiler/statements/return.lua; 1M-deep tail
        # recursion now runs on both profiles.
        source = (
            "local function loop(n, acc)\n"
            "    if n == 0 then return acc end\n"
            "    return loop(n - 1, acc + 1)\n"
            "end\n"
            "return loop(100000, 0)"
        )
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))

