"""Register allocator stress tests (Phase 2, Priority 4).

Proves allocation terminates under high pressure and that the spill-table
path (registers id >= MAX_REGS) preserves semantics.
"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

CASES = {
    'many_locals': (
        'local ' + ','.join(f'a{i}' for i in range(1, 151)) + ' = '
        + ','.join(str(i) for i in range(1, 151)) + ' '
        + 'return a1+a75+a150'),
    'deep_expression': (
        'local function f(x) return x+1 end return '
        + 'f(' * 40 + '7' + ')' * 40),
    'wide_call': (
        'local function g(...) return select("#",...) end return g('
        + ','.join(str(i) for i in range(1, 121)) + ')'),
    'many_simultaneous_calls': (
        'local function f(a,b) return a*100+b end local t={'
        + ','.join(f'f({i},{i+1})' for i in range(1, 61)) + '} return #t, t[1], t[60]'),
}


class RegisterPressureTest(unittest.TestCase):
    def test_pressure_terminates_and_preserves_semantics(self):
        for name, source in CASES.items():
            expected = LuaRuntime(unpack_returned_tuples=True).execute(source)
            for profile in ('balanced', 'hardened'):
                for seed in (42,):
                    with self.subTest(case=name, profile=profile, seed=seed), \
                            tempfile.TemporaryDirectory() as d:
                        inp, out = Path(d)/'in.lua', Path(d)/'out.lua'
                        inp.write_text(source)
                        report = protect(inp, out, profile, seed, timeout=180)
                        self.assertTrue(report['syntax_checked'])
                        actual = LuaRuntime(unpack_returned_tuples=True).execute(out.read_text())
                        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main(verbosity=2)
