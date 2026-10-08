"""RyoNex local Lua 5.1 obfuscation workbench; based on Prometheus."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
LIMIT = 8 * 1024 * 1024
CREDIT = 'Based on Prometheus by Elias Oelschner, https://github.com/prometheus-lua/Prometheus'
REVISION = 'a4efc5f381c50ae2a111bdbb9272fa3203685be3'
VERSION = '0.2.0'

def bounded_read(path):
    if not stat.S_ISREG(Path(path).stat().st_mode):
        raise ValueError('REGULAR_FILE_REQUIRED')
    with Path(path).open('rb') as f:
        data = f.read(LIMIT + 1)
    if not 0 < len(data) <= LIMIT:
        raise ValueError('FILE_SIZE_LIMIT')
    return data

def verify_vendor():
    lock = json.loads((ROOT/'vendor-lock.json').read_text())
    for name, digest in lock['files'].items():
        if not name.startswith('vendor/prometheus/'):
            continue
        path = ROOT/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('VENDOR_INTEGRITY_ERROR')

def publish(target, data):
    # Stage beside destination; an atomic hard link exposes only a complete file
    # and fails if another process won the destination name. Never overwrite.
    fd, name = tempfile.mkstemp(prefix='.ryonex-', dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(staged, target)
    finally:
        staged.unlink(missing_ok=True)

def transform(source, profile, seed):
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True, max_memory=512*1024*1024)
    lua.globals().module_path = (ROOT / 'vendor/prometheus/src/?.lua').as_posix()
    lua.execute('arg={}; package.path=module_path..";"..package.path')
    build = lua.eval('''function(source, preset, seed)
        local P = require('prometheus')
        P.Logger.logLevel = -1
        P.Logger.errorCallback = function() error('ENGINE_PARSE_OR_TRANSFORM_ERROR') end
        local T = require('prometheus.tokenizer')
        local tokenizer = T:new({LuaVersion='Lua51'})
        tokenizer:append(source)
        -- Introspection guard (see docs/INTROSPECTION_POLICY.md): reject every
        -- statically visible route to setfenv/getfenv/debug. Direct identifiers
        -- (including field/variable positions) and constant-string index keys,
        -- including literal concatenations inside one index. Fully dynamic
        -- resolution is unsupported input that this scan cannot always detect.
        local forbidden = {setfenv=true, getfenv=true, debug=true}
        local tokens = tokenizer:scanAll()
        for _, token in ipairs(tokens) do
            if token.kind == T.TokenKind.Ident and forbidden[token.value] then
                error('UNSUPPORTED_RUNTIME_INTROSPECTION')
            end
        end
        local stack = {}
        for _, token in ipairs(tokens) do
            if token.kind == T.TokenKind.Symbol then
                if token.value == '[' then
                    stack[#stack + 1] = {}
                elseif token.value == ']' then
                    local parts = table.remove(stack)
                    if parts and forbidden[table.concat(parts)] then
                        error('UNSUPPORTED_RUNTIME_INTROSPECTION')
                    end
                elseif #stack > 0 then
                    if token.value ~= '..' and token.value ~= '(' and token.value ~= ')' then
                        stack[#stack] = {}
                    end
                end
            elseif #stack > 0 then
                if token.kind == T.TokenKind.String then
                    table.insert(stack[#stack], token.value)
                else
                    stack[#stack] = {}
                end
            end
        end
        local c = P.Presets[preset]
        c.Seed = seed
        c.LuaVersion = 'Lua51'
        -- Debug/anti-tamper assumptions are not portable across host runtimes.
        local steps = {}
        for _, step in ipairs(c.Steps) do
            if step.Name ~= 'AntiTamper' then steps[#steps+1] = step end
        end
        c.Steps = steps
        return P.Pipeline:fromConfig(c):apply(source, 'input.lua')
    end''')
    result = build(source, {'balanced':'Medium', 'hardened':'Strong'}[profile], seed)
    # Compile only: never execute the customer's input or output during a build.
    valid = lua.eval('function(s) return loadstring(s) ~= nil end')(result)
    if not valid:
        raise ValueError('OUTPUT_SYNTAX_ERROR')
    return result

def worker(args):
    try:
        data = bounded_read(args.input)
        result = transform(data.decode('utf-8'), args.profile, args.seed).encode('utf-8')
        if not 0 < len(result) <= LIMIT:
            raise ValueError('OUTPUT_SIZE_LIMIT')
        Path(args.output).write_bytes(result)
        return 0
    except UnicodeDecodeError:
        return 4
    except Exception as e:
        # Parser exceptions can contain source snippets: keep them out of logs.
        if 'UNSUPPORTED_RUNTIME_INTROSPECTION' in str(e):
            return 3
        return 2

def protect(input_path, output_path, profile='balanced', seed=None, timeout=120):
    if Path(output_path).is_symlink():
        raise ValueError('OUTPUT_SYMLINK')
    source, target = Path(input_path).resolve(), Path(output_path).resolve()
    if source == target:
        raise ValueError('INPUT_OUTPUT_MUST_DIFFER')
    if target.exists():
        raise ValueError('OUTPUT_EXISTS')
    if profile not in ('balanced', 'hardened') or type(timeout) is not int or not 1 <= timeout <= 720:
        raise ValueError('INVALID_CONFIG')
    seed = secrets.randbelow(2147483646)+1 if seed is None else seed
    if type(seed) is not int or not 1 <= seed <= 2147483647:
        raise ValueError('INVALID_SEED')
    data = bounded_read(source)
    verify_vendor()
    target.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ryonex-obf-') as work:
        src, out = Path(work)/'input.lua', Path(work)/'output.lua'
        src.write_bytes(data)
        command = [sys.executable, str(ROOT/'engine.py'), '_worker', str(src),
                   str(out), '--profile', profile, '--seed', str(seed)]
        try:
            p = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout, shell=False,
                               env={k:v for k,v in os.environ.items() if k in
                                    ('PATH','SystemRoot','SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL')})
        except subprocess.TimeoutExpired:
            raise ValueError('BUILD_TIMEOUT') from None
        if p.returncode == 3:
            raise ValueError('UNSUPPORTED_RUNTIME_INTROSPECTION')
        if p.returncode == 4:
            raise ValueError('INPUT_MUST_BE_UTF8')
        if p.returncode or not out.is_file():
            raise ValueError('BUILD_FAILED_OR_UNSUPPORTED_SYNTAX')
        data = bounded_read(out)
        publish(target, data)
    return {'engine':'ryonex-prometheus-adapter', 'version':VERSION,
            'upstream_commit':REVISION, 'profile':profile, 'seed':seed,
            'target':'Lua51', 'output_bytes':len(data),
            'output_sha256':hashlib.sha256(data).hexdigest(),
            'seconds':round(time.monotonic()-start, 3),
            'syntax_checked':True, 'semantic_equivalence_checked':False,
            'luraph_comparison':'NOT_TESTED'}

def main():
    p = argparse.ArgumentParser(description='RyoNex OBF Lab '+VERSION+'. '+CREDIT)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('protect', '_worker'):
        a = sub.add_parser(name)
        a.add_argument('input'); a.add_argument('output')
        a.add_argument('--profile', choices=['balanced','hardened'], default='balanced')
        a.add_argument('--seed', type=int)
        a.add_argument('--timeout', type=int, default=120)
    sub.add_parser('doctor')
    args = p.parse_args()
    if args.command == 'doctor':
        try:
            from lupa.lua51 import LuaRuntime
            LuaRuntime()
            verify_vendor()
        except Exception:
            print('NOT_READY: install requirements.txt and restore vendor sources'); return 2
        print('READY: Lua 5.1 local build; Luau/Roblox and Luraph comparison NOT VERIFIED')
        print(CREDIT); return 0
    if args.command == '_worker':
        return worker(args)
    try:
        report = protect(args.input, args.output, args.profile, args.seed, args.timeout)
        print(json.dumps(report, indent=2)); return 0
    except (ValueError, OSError) as e:
        print(str(e) if isinstance(e, ValueError) else 'FILE_IO_ERROR', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
