"""Reproducible local performance measurements, not security scores."""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import ROOT,protect
from lupa.lua51 import LuaRuntime

SOURCE='local n=0;for i=1,5000 do n=n+(i%17) end;return n'
def measure(source):
    lua=LuaRuntime(unpack_returned_tuples=True)
    fn=lua.eval('function(...) '+source+' end')
    expected=fn();times=[]
    for _ in range(9):
        start=time.perf_counter();result=fn();times.append(time.perf_counter()-start)
        assert result==expected
    return {'return_value':expected,'median_seconds':statistics.median(times),'runs':len(times),'bytes':len(source.encode())}

if __name__=='__main__':
    records={'source':measure(SOURCE),'environment':'Lupa Lua 5.1, this container; not Roblox or user PC','profiles':{}}
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'in.lua';p.write_text(SOURCE)
        for profile in ['balanced','hardened']:
            q=Path(d)/(profile+'.lua');build=protect(p,q,profile,42)
            m=measure(q.read_text());assert m['return_value']==records['source']['return_value']
            m['runtime_ratio']=m['median_seconds']/records['source']['median_seconds']
            m['size_ratio']=m['bytes']/records['source']['bytes'];m['build_seconds']=build['seconds']
            records['profiles'][profile]=m
    (ROOT/'evidence/benchmark.json').write_text(json.dumps(records,indent=2))
    print(json.dumps(records,indent=2))
