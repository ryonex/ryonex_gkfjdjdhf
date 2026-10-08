"""Phase 1 (0.3.0) regression tests for confirmed semantic defects.

Every confirmed defect found by tests/difffuzz.py gets a minimized regression
here. Non-semantic boundaries (error position strings, __gc execution order)
are asserted modulo their documented normalization.

Known open defects are marked expectedFailure: a future "unexpected success"
is the signal to remove the marker and update docs/RYONEX_030_PROGRESS.md.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

PROFILES = ('balanced', 'hardened')


def run(source):
    lua = LuaRuntime(unpack_returned_tuples=True)
    fn = lua.eval('function(...) ' + source + ' end')
    return fn()


def build_and_run(source, profile, seed=1):
    with tempfile.TemporaryDirectory() as d:
        inp, out = Path(d) / 'in.lua', Path(d) / 'out.lua'
        inp.write_text(source)
        protect(inp, out, profile, seed)
        return run(out.read_text())


class FinalizerUpvalueRegression(unittest.TestCase):
    """0.3.0 defect: closures reachable only via userdata __gc finalizers
    read freed upvalues under the old refcount+__gc model (Lua 5.1 runs
    finalizers in unspecified order; the entries-proxy __gc could decrement
    before the user finalizer read). Fixed by the box-based upvalue model."""

    CASES = {
        'loopvar_capture': '''
            local fired = {}
            for i = 1, 5 do
                local p = newproxy(true)
                getmetatable(p).__gc = function() fired[i] = true end
                p = nil
            end
            collectgarbage('collect'); collectgarbage('collect')
            local n = 0
            for i = 1, 5 do if fired[i] then n = n + 1 end end
            return n''',
        'param_capture': '''
            local fired = {}
            local function mk(i)
                local p = newproxy(true)
                getmetatable(p).__gc = function() fired[i] = true end
                p = nil
            end
            mk(1); mk(2); mk(3)
            collectgarbage('collect'); collectgarbage('collect')
            return fired[1], fired[2], fired[3]''',
        'sibling_state_sharing': '''
            local E, O, U = 5, 2, {}
            local function V()
                if #U == 0 then
                    E = ((E * 213 + 27552854471197)) % 35184372088832
                    O = (O * 201) % 257
                    U = {E % 256, O % 256}
                end
                local r = #U
                local k = U[r]
                U[r] = nil
                return k
            end
            local function decode(n, seed)
                U = {}; E = seed % 35184372088832; O = seed % 255 + 2
                local b, p = 204, {}
                for O = 1, n do
                    b = (1 + V() + b) % 256
                    p[#p + 1] = b
                end
                return unpack(p)
            end
            return decode(7, 22126428612671)''',
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
        source = '''
            local out = {}
            for k = 1, 4 do
                local i = k
                local p = newproxy(true)
                getmetatable(p).__gc = function() out[#out + 1] = i end
                p = nil
            end
            collectgarbage('collect'); collectgarbage('collect')
            table.sort(out)
            return table.concat(out, ',')'''
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))

    def test_error_position_normalized(self):
        # error(msg) with default level embeds source positions that cannot
        # survive transformation; the message text must match modulo the
        # position prefix.
        source = '''
            local ok, e = pcall(function() error('boom') end)
            return ok, (e:gsub('^.-:%d+: ', ''))'''
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))


class KnownDefects(unittest.TestCase):
    @unittest.expectedFailure
    def test_deep_tail_call_is_proper(self):
        # KNOWN DEFECT (0.3.0): the VM call path does not implement proper
        # tail calls; recursion depth beyond the Lua stack (~15-20k nested
        # container frames) raises 'stack overflow' where Lua 5.1 tail
        # recursion runs unbounded. Tracked in docs/RYONEX_030_PROGRESS.md.
        source = '''
            local function loop(n, acc)
                if n == 0 then return acc end
                return loop(n - 1, acc + 1)
            end
            return loop(20000, 0)'''
        expected = run(source)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(expected, build_and_run(source, profile))
