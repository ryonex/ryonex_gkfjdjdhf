"""Differential tests on authored fixtures; never run customer scripts here."""
import json
import math
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import protect, ROOT
from test_engine import CASES
from lupa.lua51 import LuaRuntime
from lupa.luajit21 import LuaRuntime as LuaJITRuntime

EXTRA = {
 'paren_assignment': 'local function f()return 1,2 end;local a,b=(f());return a,b',
 'paren_table': 'local function f()return 1,2 end;local t={(f())};return #t,t[1],t[2]',
 'paren_vararg': 'local function f(...)return select("#",(...)) end;return f(1,2)',
 'trailing_nil': 'local function f()return 1,nil end;return select("#",f())',
 'nil_vararg_count': 'local function f(...)return select("#",...) end;return f(1,nil,nil)',
 'short_circuit': 'local n=0;local function f() n=n+1;return n end;local a=false and f();local b=true or f();local c=nil or f();return n,a,b,c',
 'assignment': 'local a,b=1,2;a,b=b,a;local t={};local i=1;t[i],i=9,2;return a,b,t[1],i',
 'multi_return': 'local function f() return 1,nil,3 end;local a,b,c=f();local t={f()};return a,b,c,t[1],t[2],t[3],select("#",f())',
 'parenthesized_return': 'local function f() return 1,2 end;return select("#",(f())),select("#",f())',
 'upvalue_siblings': 'local x=1;local function a() x=x+2 end;local function b() return x end;a();a();return b()',
 'closure_loop': 'local t={};for i=1,4 do local x=i;t[i]=function() return x end end;return t[1](),t[2](),t[3](),t[4]()',
 'mutual_recursion': 'local a,b;a=function(n) if n==0 then return true end return b(n-1) end;b=function(n) if n==0 then return false end return a(n-1) end;return a(12),a(13)',
 'method_self': 'local t={x=3};function t:f(y) self.x=self.x+y;return self.x end;return t:f(4),t.x',
 'metamethod_add': 'local m={__add=function(a,b)return a.x+b.x end};return setmetatable({x=3},m)+setmetatable({x=8},m)',
 'metamethod_call': 'local t=setmetatable({},{__call=function(_,a,b)return a*b end});return t(3,7)',
 'metamethod_newindex': 'local z=0;local t=setmetatable({},{__newindex=function(t,k,v) z=z+v end});t.x=3;t.y=4;return z',
 'generic_iterator': 'local function it(s,k) k=k+1;if k<=s then return k,k*k end end;local n=0;for k,v in it,5,0 do n=n+v end;return n',
 'negative_for': 'local n=0;for i=10,1,-2 do n=n+i end;return n',
 'repeat_scope': 'local n=0;repeat local x=n+1;n=x until x==4;return n',
 'break_nested': 'local n=0;for i=1,5 do for j=1,8 do if j==3 then break end;n=n+1 end end;return n',
 'false_nil': 'local t={false,nil,0,""};return t[1],t[2],not t[3],not t[4]',
 'concat_length': 'return #( "a".."b"), "a" .. 2 .. "b"',
 'long_string': 'local x=[==[a\nb ]=] c]==];return x,#x',
 'binary_escape': 'local x="\\000\\001\\127\\128\\255";return #x,string.byte(x,1,5)',
 'unicode': 'local x="สวัสดี";return #x,x',
 'select_negative': 'local function f(...) return select(-2,...) end;return f(1,2,3,4)',
 'table_keys': 'local t={[false]=4,[true]=5,[2.5]=6};return t[false],t[true],t[2.5]',
 'xpcall': 'local ok,e=xpcall(function() error("x") end,function(e)return "handled" end);return ok,e',
 'coroutine_values': 'local c=coroutine.create(function(x)local y=coroutine.yield(x+1);return y+2 end);local a,b=coroutine.resume(c,3);local d,e=coroutine.resume(c,9);return a,b,d,e',
 'vararg_capture': 'local function f(...) local t={...};local n=select("#",...);return function() return n,unpack(t,1,n) end end;return f(1,nil,3)()',
 'precedence': 'return -2^2,2^3^2,(-2)^2,5%-3,-5%3',
 'nan_infinity': 'local n=0/0;return n~=n,1/0,-1/0',
}

def observe(source, runtime=LuaRuntime):
    lua=runtime(unpack_returned_tuples=True, max_memory=128*1024*1024)
    # Instruction budget bounds bad transformed loops on trusted fixtures.
    lua.execute('debug.sethook(function() error("TEST_INSTRUCTION_LIMIT") end,"",5000000)')
    value=lua.execute('return (function(...) return select("#",...),... end)((function(...) '+source+' end)())')
    values=value if isinstance(value,tuple) else (value,)
    return [repr(v) for v in values]

def main():
    cases={**CASES,**EXTRA}
    rng=random.Random(20261009)
    for i in range(40):
        a,b,c=[rng.randint(-100,100) for _ in range(3)]
        count=rng.randint(1,30)
        cases['generated_'+str(i)]=f'local x={a};for i=1,{count} do if i%3==0 then x=x+({b}) else x=x-({c}) end end;return x,x%7'
    report={'seed_schedule':[1,42,314159], 'records':[], 'failures':[]}
    for name,source in cases.items():
        try: expected={name:observe(source,rt) for name,rt in [('lua51',LuaRuntime),('luajit21',LuaJITRuntime)]}
        except Exception as e:
            report['failures'].append({'case':name,'phase':'fixture','error':str(e)});continue
        for profile in ('balanced','hardened'):
            for seed in report['seed_schedule']:
                with tempfile.TemporaryDirectory() as d:
                    inp,out=Path(d)/'in.lua',Path(d)/'out.lua';inp.write_text(source)
                    try:
                        build=protect(inp,out,profile,seed,timeout=30)
                        for runtime_name,rt in [('lua51',LuaRuntime),('luajit21',LuaJITRuntime)]:
                            started=time.perf_counter();actual=observe(out.read_text(),rt);elapsed=time.perf_counter()-started
                            if actual!=expected[runtime_name]: raise AssertionError(f'{runtime_name}: {expected[runtime_name]} != {actual}')
                            report['records'].append({'case':name,'profile':profile,'seed':seed,'runtime':runtime_name,'bytes':build['output_bytes'],'build_seconds':build['seconds'],'execution_with_runtime_setup_seconds':elapsed})
                    except Exception as e:
                        report['failures'].append({'case':name,'profile':profile,'seed':seed,'error':str(e)})
        print(name,flush=True)
    report['passed']=len(report['records']);report['failed']=len(report['failures'])
    (ROOT/'evidence/stress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'passed':report['passed'],'failed':report['failed'],'failures':report['failures']},ensure_ascii=False,indent=2))
    return bool(report['failed'])

if __name__=='__main__':raise SystemExit(main())
