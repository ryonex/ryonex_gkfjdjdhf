"""Regression tests for the introspection guard (see docs/INTROSPECTION_POLICY.md)."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect

REJECTED = {
    'direct_setfenv': 'local f=function() return x end;setfenv(f,{x=42});return f()',
    'direct_getfenv': 'return getfenv()',
    'direct_debug': 'return debug.getinfo(1)',
    'field_access': 'local t={};return t.debug',
    'computed_literal': 'local g=_G;return g["setfenv"]',
    'computed_getfenv': 'return _G["getfenv"]',
    'computed_debug': 'return _G["debug"]',
    'computed_concat': 'local g=_G;return g["set".."fenv"]',
    'computed_concat3': 'return _G[("d").."".."ebug"]',
    'computed_paren': 'return _G[("setfenv")]',
    'computed_long_string': 'return _G[ [[debug]] ]',
    'computed_field_style': 'local t=_G;return t["getfenv"]',
}

ACCEPTED = {
    'words_in_strings': '-- getfenv\nreturn "setfenv debug"',
    'ordinary_computed': 'local k="x";local t={[k]=1};return t[k]',
    'string_concat_value': 'local s="set".."fenv";return #s',
    'plain_code': 'local n=0;for i=1,3 do n=n+i end;return n',
}


class GuardTest(unittest.TestCase):
    def _protect(self, source):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d)/'in.lua', Path(d)/'out.lua'
            inp.write_text(source)
            return protect(inp, out, 'balanced', 42)

    def test_rejected(self):
        for name, source in REJECTED.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError) as ctx:
                    self._protect(source)
                self.assertIn('UNSUPPORTED_RUNTIME_INTROSPECTION', str(ctx.exception))

    def test_accepted(self):
        for name, source in ACCEPTED.items():
            with self.subTest(case=name):
                self._protect(source)

    def test_dynamic_alias_undetectable_is_documented(self):
        # Boundary per policy: value-flow across statements is not detected.
        # This test pins the boundary; do NOT treat it as a guarantee.
        source = 'local s="setfenv";local g=_G;local f=g[s];return type(f)'
        self._protect(source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
