"""Regression tests for numeric-literal corruption found in Phase 2 (P0).

Bugs: unparser tostring() rounding (>14 significant digits) and
NumbersToExpressions NumberRepresentationMutation hex/scientific
truncation (hardened profile).
"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

CASES = {
    'big_int': 'return 123456789012345678',
    'int_1e15_plus': 'return 1000000000000001',
    'int_2_53': 'return 9007199254740992',
    'encrypt_consts': 'return 35184372088832, 17592186044415, 4294967296',
    'hex_candidates': 'return 0x1FFFFFFFF, 0xFFFFFFFF, 255, 65536',
    'float_precision': 'return 0.1, 0.3333333333333333',
    'negative_big': 'return -9007199254740992',
    'mixed_table': ('local t={35184372088832,4294967296,255,257,1099511627776,'
                    '1000000000000001} return t[1],t[2],t[3]*t[4],t[5],t[6]'),
    'zero_and_small': 'return 0, 1, 10, 100, 2^24',
}


class NumericPrecisionTest(unittest.TestCase):
    def test_literal_roundtrip(self):
        for name, source in CASES.items():
            expected = LuaRuntime(unpack_returned_tuples=True).execute(source)
            for profile in ('balanced', 'hardened'):
                for seed in (42, 314159):
                    with self.subTest(case=name, profile=profile, seed=seed), \
                            tempfile.TemporaryDirectory() as d:
                        inp, out = Path(d)/'in.lua', Path(d)/'out.lua'
                        inp.write_text(source)
                        protect(inp, out, profile, seed)
                        actual = LuaRuntime(unpack_returned_tuples=True).execute(out.read_text())
                        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main(verbosity=2)
