import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

# Trusted, authored fixtures only. This is not a sandbox for uploaded scripts.
CASES = {
    'closures': 'local function f(x) return function(y) x=x+y; return x end end local g=f(4); return g(3),g(5)',
    'nil_varargs': 'local function f(...) return select("#",...),... end return f(1,nil,3)',
    'recursion': 'local function f(n) if n<2 then return 1 end return n*f(n-1) end return f(7)',
    'metatable': 'local t=setmetatable({x=4},{__index=function(t,k) return 9 end}); return t.x+t.y',
    'loops': 'local n=0; for i=1,12 do if i%2==0 then n=n+i end end repeat n=n-1 until n<30; return n',
    'strings': 'local s="hello\\000world"; return #s,string.byte(s,6),s:sub(7)',
    'pcall': 'local ok,e=pcall(function() error("fixture") end); return ok,type(e)',
    'coroutine': 'local c=coroutine.create(function() coroutine.yield(4); return 7 end); local a,b=coroutine.resume(c); local d,e=coroutine.resume(c); return a,b,d,e',
}

class EngineTest(unittest.TestCase):
    def test_semantics(self):
        for name, source in CASES.items():
            expected = LuaRuntime(unpack_returned_tuples=True).execute(source)
            for profile in ('balanced','hardened'):
                for seed in (42,314159):
                    with self.subTest(case=name, profile=profile, seed=seed), tempfile.TemporaryDirectory() as d:
                        inp, out = Path(d)/'in.lua', Path(d)/'out.lua'
                        inp.write_text(source)
                        protect(inp, out, profile, seed)
                        actual = LuaRuntime(unpack_returned_tuples=True).execute(out.read_text())
                        self.assertEqual(expected, actual)

    def test_reproducibility_and_diversity(self):
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/'in.lua'; inp.write_text('return 37,"hello"')
            values=[]
            for i, seed in enumerate((42,42,43)):
                out=Path(d)/f'{i}.lua'; protect(inp,out,seed=seed); values.append(out.read_bytes())
            self.assertEqual(values[0],values[1]); self.assertNotEqual(values[0],values[2])

    def test_invalid_input_no_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            inp,out=Path(d)/'in.lua',Path(d)/'out.lua'; inp.write_text('local = broken')
            with self.assertRaises(ValueError): protect(inp,out)
            self.assertFalse(out.exists())

    def test_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'in.lua'; p.write_text('return 1')
            with self.assertRaises(ValueError): protect(p,p)
            self.assertEqual(p.read_text(),'return 1')

if __name__=='__main__': unittest.main(verbosity=2)
