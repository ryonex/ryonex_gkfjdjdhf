"""Upvalue lifetime and reference-counting regression tests.

Covers shared upvalues, nested closures, escaping closures, coroutine
interaction, garbage collection, and closure lifetime after the parent
function returned (Phase 2, Priority 3).
"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

CASES = {
    'shared_two_closures': (
        'local x=1;local function a() x=x+2 end;local function b() return x end;'
        'a();a();return b()'),
    'shared_three_closures': (
        'local x=0;local inc=function() x=x+1 end;local dec=function() x=x-1 end;'
        'local get=function() return x end;inc();inc();inc();dec();return get()'),
    'nested_three_levels': (
        'local function g() local a=1 local function p() local b=2 local function c() '
        'return a+b end return c end return p end local f=g()() return f()'),
    'escaping_after_parent_return': (
        'local function mk() local n=0 return function() n=n+1 return n end end '
        'local f=mk() local g=mk() f();f();return f(),g()'),
    'loop_closures_with_gc': (
        'local t={} for i=1,6 do local x=i*2 t[i]=function() return x end '
        'if i%2==0 then collectgarbage("collect") end end '
        'return t[1](),t[6](),t[3]()'),
    'gc_before_call': (
        'local function mk(x) return function(y) return x+y end end '
        'local keep=mk(10) do local a,b,c=mk(1),mk(2),mk(3) end '
        'collectgarbage("collect") collectgarbage("collect") return keep(5)'),
    'gc_after_partial_use': (
        'local fs={} for i=1,8 do local x=i fs[i]=function() return x end end '
        'local s1=fs[1]() local s4=fs[4]() collectgarbage("collect") '
        'return s1,s4,fs[8](),fs[2]()'),
    'long_lived_among_short_lived': (
        'local x=0 local hold=function() return x end '
        'for i=1,20 do local y=i local f=function() y=y+1 return y end f() end '
        'x=42 collectgarbage("collect") return hold()'),
    'coroutine_upvalue_persistence': (
        'local x=5 local c=coroutine.create(function() '
        'local f=function() x=x+1 return x end coroutine.yield(f()) '
        'coroutine.yield(f()) end) '
        'local a,b=coroutine.resume(c) local d,e=coroutine.resume(c) '
        'return a,b,d,e,x'),
    'closure_passed_between_coroutines': (
        'local n=0 local f=function() n=n+1 return n end '
        'local c=coroutine.create(function(g) return g() end) '
        'local a,b=coroutine.resume(c,f) local d=coroutine.resume(c,f) '
        'return a,b,n'),
    'upvalue_rebind_after_closure_created': (
        'local x=1 local f=function() return x end local old=f x=99 '
        'return old(), f()'),
    'method_upvalue_self': (
        'local t={v=1} function t:add(k) local s=self return function() '
        's.v=s.v+k return s.v end end local f=t:add(4) local g=t:add(10) '
        'return f(),g(),t.v'),
}


class UpvalueLifetimeTest(unittest.TestCase):
    def test_lifetime(self):
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
