"""Systematic Lua 5.1 multi-value/arity differential matrix.

Categories: multiple returns, parenthesized expressions, varargs, tail calls,
table constructors, argument adjustment, assignment adjustment, nested
expressions, closures. Each case is compared against the original semantics
including trailing nils (via select('#', ...)).
"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect
from lupa.lua51 import LuaRuntime

CASES = {
    # multiple return values
    'multi_basic': 'local function f() return 1,2,3 end return f()',
    'multi_trailing_nil': 'local function f() return 1,nil end return select("#",f()),f()',
    'multi_only_nil': 'local function f() return nil,nil end return select("#",f())',
    'multi_none': 'local function f() end return select("#",f()), 9',
    # parenthesized expressions
    'paren_call_arg': 'local function f() return 1,2 end return select("#",(f()))',
    'paren_call_return': 'local function f() return 1,2 end return (f())',
    'paren_call_nested': 'local function f() return 1,2 end return select("#",((f())))',
    'paren_vararg': 'local function g(...) return select("#",(...)) end return g(1,2,3)',
    'paren_table': 'local function f() return 1,2 end local t={(f())} return #t,t[1]',
    # varargs
    'vararg_spread': 'local function f(...) return ... end return f(1,nil,3)',
    'vararg_count': 'local function f(...) return select("#",...),... end return f(1,nil,nil)',
    'vararg_mid': 'local function f(...) local a,b = ... return a,b end return f(7,8,9)',
    'vararg_tail_call': 'local function f(...) return ... end local function g(...) return f(...) end return g(4,5)',
    # tail calls
    'tail_basic': 'local function f() return 1,2 end local function g() return f() end return g()',
    'tail_vararg': 'local function f(...) return ... end local function g(...) return f(9,...) end return g(1,2)',
    'tail_select': 'local function f() return 1,2 end local function g() return select("#",f()) end return g()',
    # table constructors
    'table_last_call': 'local function f() return 1,2,3 end local t={f()} return #t,t[3]',
    'table_mid_call': 'local function f() return 1,2,3 end local t={f(),9} return #t,t[1],t[2],t[3]',
    'table_paren_call': 'local function f() return 1,2,3 end local t={(f()),9} return #t,t[1],t[2]',
    'table_vararg': 'local function g(...) return {...}, select("#",...) end local t,n = g(1,nil,3) return #t,n',
    # function argument adjustment
    'arg_mid_call': 'local function f() return 1,2 end local function h(a,b,c) return a,b,c end return h(f(),9)',
    'arg_last_call': 'local function f() return 1,2 end local function h(...) return select("#",...) end return h(f())',
    'arg_paren_last': 'local function f() return 1,2 end local function h(...) return select("#",...) end return h((f()))',
    'arg_vararg_tail': 'local function f(...) return ... end local function h(...) return select("#",...) end return h(f(1,nil),2)',
    # assignment adjustment
    'assign_exact': 'local function f() return 1,2 end local a,b = f() return a,b',
    'assign_trunc': 'local function f() return 1,2 end local a = f() return a',
    'assign_pad': 'local function f() return 1 end local a,b = f() return a,b',
    'assign_paren': 'local function f() return 1,2 end local a,b = (f()) return a,b',
    'assign_swap': 'local a,b = 1,2 a,b = b,a return a,b',
    # nested expressions
    'nested_args': 'local function f(...) return ... end local function g(...) return f((...)) end return g(1,2)',
    'nested_select': 'local function f() return 1,2 end return select("#",select(1,f()))',
    'nested_paren_binop': 'local function f() return 4 end return (f())+1',
    # closures
    'closure_multi_return': ('local function mk() local x=1 return function() return x, x+1 end end '
                             'local f=mk() return f()'),
    'closure_varargs': ('local function mk(...) local t={...} return function() return #t, t[2] end end '
                        'local f=mk(1,2,3) return f()'),
    'closure_upvalue_nil': ('local function mk() local x return function() return x, 5 end end '
                            'local f=mk() return select("#",f()), f()'),
}


def observe(source):
    lua = LuaRuntime(unpack_returned_tuples=True, max_memory=128*1024*1024)
    lua.execute('debug.sethook(function() error("TEST_INSTRUCTION_LIMIT") end,"",5000000)')
    value = lua.execute('return (function(...) return select("#",...),... end)((function(...) '
                        + source + ' end)())')
    values = value if isinstance(value, tuple) else (value,)
    return [repr(v) for v in values]


class ArityMatrixTest(unittest.TestCase):
    def test_matrix(self):
        for name, source in CASES.items():
            expected = observe(source)
            for profile in ('balanced', 'hardened'):
                for seed in (42, 314159):
                    with self.subTest(case=name, profile=profile, seed=seed), \
                            tempfile.TemporaryDirectory() as d:
                        inp, out = Path(d)/'in.lua', Path(d)/'out.lua'
                        inp.write_text(source)
                        protect(inp, out, profile, seed)
                        actual = observe(out.read_text())
                        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main(verbosity=2)
