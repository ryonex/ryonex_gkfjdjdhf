"""Reproducible performance matrix for balanced vs hardened (Phase 2).

Measures runtime overhead, output size, build duration and Lua heap usage on
identical workloads, plus the isolated cost of double virtualization. These
are measurements, not security scores; double virtualization is NOT claimed
to add protection (no controlled resistance evaluation exists).
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import protect
from lupa.lua51 import LuaRuntime

WORKLOADS = {
    'loop': 'local n=0 for i=1,5000 do n=n+(i%17) end return n',
    'string_ops': ('local s="" for i=1,200 do s=s.."ab" if #s>50 then s=s:sub(1,20) end end '
                   'return #s,string.byte(s,1)'),
    'table_ops': ('local t={} for i=1,1000 do t[#t+1]=i*2 end local n=0 '
                  'for i=1,#t do n=n+t[i] end return n'),
    'closures': ('local fs={} for i=1,200 do local x=i fs[i]=function(y) return x+y end end '
                 'local n=0 for i=1,200 do n=n+fs[i](i) end return n'),
    'calls': ('local function f(a,b) return a+b end local n=0 '
              'for i=1,1000 do n=f(n,i) end return n'),
}


def measure(source):
    lua = LuaRuntime(unpack_returned_tuples=True)
    fn = lua.eval('function(...) ' + source + ' end')
    expected = fn()
    times = []
    for _ in range(9):
        start = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - start)
        assert result == expected
    heap_kb = lua.eval('collectgarbage("count")')
    return {'value': expected, 'median_seconds': statistics.median(times),
            'bytes': len(source.encode()), 'lua_heap_kb': round(heap_kb, 1)}


def raw_build(source, steps, seed=42):
    """Build with an explicit step list (for isolated VM-layer cost tests)."""
    prelude = '''function(source, steps, seed)
        local P = require('prometheus')
        P.Logger.logLevel = -1
        P.Logger.errorCallback = function() error('ENGINE_PARSE_OR_TRANSFORM_ERROR') end
        local cfg = { Seed = seed, LuaVersion = 'Lua51', PrettyPrint = false,
                      Steps = steps, Name = 'bench' }
        return P.Pipeline:fromConfig(cfg):apply(source, 'input.lua')
    end'''
    lua = LuaRuntime(unpack_returned_tuples=True, max_memory=512*1024*1024)
    lua.globals().module_path = (ROOT / 'vendor/prometheus/src/?.lua').as_posix()
    lua.execute('arg={}; package.path=module_path..";"..package.path')
    fn = lua.eval(prelude)
    tbl = lua.table()
    for i, (name, settings) in enumerate(steps, 1):
        tbl[i] = lua.table_from({'Name': name, 'Settings': lua.table_from(settings or {})})
    return fn(source, tbl, seed)


if __name__ == '__main__':
    records = {'note': 'Local machine, Lupa Lua 5.1; not Roblox. No security claims.',
               'workloads': {}}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for name, source in WORKLOADS.items():
            base = measure(source)
            entry = {'original': base, 'profiles': {}}
            inp = d/(name + '_in.lua')
            inp.write_text(source)
            for profile in ('balanced', 'hardened'):
                out = d/(name + '_' + profile + '.lua')
                t0 = time.perf_counter()
                build = protect(inp, out, profile, 42)
                build_seconds = time.perf_counter() - t0
                m = measure(out.read_text())
                entry['profiles'][profile] = {
                    'median_seconds': m['median_seconds'],
                    'runtime_ratio': m['median_seconds']/base['median_seconds'],
                    'bytes': m['bytes'],
                    'size_ratio': m['bytes']/base['bytes'],
                    'build_seconds': round(build_seconds, 3),
                    'lua_heap_kb': m['lua_heap_kb'],
                    'heap_ratio': m['lua_heap_kb']/base['lua_heap_kb'],
                    'value_matches': m['value'] == base['value'],
                }
            records['workloads'][name] = entry
            print(name, 'done', flush=True)

    # Isolated double-virtualization cost: one VM layer vs two, identical
    # workload, identical remaining steps excluded. Measurement only.
    single = raw_build(WORKLOADS['loop'], [('Vmify', {})], 42)
    double = raw_build(WORKLOADS['loop'],
                       [('Vmify', {}), ('EncryptStrings', {}), ('Vmify', {})], 42)
    m1, m2 = measure(single), measure(double)
    orig = measure(WORKLOADS['loop'])
    records['double_virtualization'] = {
        'workload': 'loop',
        'single_vm': {'runtime_ratio': m1['median_seconds']/orig['median_seconds'],
                      'bytes': m1['bytes']},
        'double_vm': {'runtime_ratio': m2['median_seconds']/orig['median_seconds'],
                      'bytes': m2['bytes']},
        'double_over_single': m2['median_seconds']/m1['median_seconds'],
        'claim': 'NONE: cost measured; added resistance NOT evaluated',
    }
    path = ROOT/'evidence/benchmark_v2.json'
    path.write_text(json.dumps(records, indent=2))
    print(json.dumps(records, indent=2))

