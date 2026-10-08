"""Deterministic differential semantic fuzzer for the RYONEX transformer.

Phase 1 (0.3.0): generates Lua 5.1 programs from a typed grammar focused on
multi-return adjustment, parenthesized expressions, varargs, closures,
upvalues, coroutines, tail calls, metamethods, error propagation,
environment-sensitive operations and GC behavior. Every program is executed
(a) directly and (b) after protection (balanced/hardened) on the same Lua 5.1
runtime; a full value trace must match.

A mismatch is a defect: the failing seed + source are printed and must become
a regression test in tests/test_phase1_regressions.py after minimization.

Usage:
    python tests/difffuzz.py [--programs N] [--seed S]
                             [--profiles balanced,hardened] [--keep DIR]

Determinism rules for generated programs (also enforced by the generator):
- no pairs()-dependent output ordering; sequences only
- no table identity / tostring(table) in traces
- error position prefixes are normalized (protected code has other lines)
- GC probes only emit state after forced full collections
"""
import argparse
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import transform  # noqa: E402
from lupa.lua51 import LuaRuntime  # noqa: E402

# ---------------------------------------------------------------- harness --

HARNESS_PRE = r'''
local __log = {}
local __seen = {}
local function __canon(v, d)
    d = d or 0
    local t = type(v)
    if t == 'number' then
        return string.format('n:%.17g', v)
    elseif t == 'string' then
        return 's:' .. #v .. ':' .. v
    elseif t == 'boolean' then
        return v and 'b:true' or 'b:false'
    elseif t == 'nil' then
        return 'nil'
    elseif t == 'table' then
        if d > 5 then return 't:...' end
        if __seen[v] then return 't:cycle' end
        __seen[v] = true
        local keys = {}
        for k in pairs(v) do keys[#keys + 1] = k end
        table.sort(keys, function(a, b)
            local ta, tb = type(a), type(b)
            if ta ~= tb then return ta < tb end
            if ta == 'number' or ta == 'string' then return a < b end
            return tostring(a) < tostring(b)
        end)
        local out = {}
        for i = 1, #keys do
            out[i] = __canon(keys[i], d + 1) .. '=' .. __canon(rawget(v, keys[i]), d + 1)
        end
        __seen[v] = nil
        return 't:{' .. table.concat(out, ',') .. '}'
    elseif t == 'function' then
        return 'f'
    elseif t == 'thread' then
        return 'coroutine'
    else
        return t
    end
end
local function __emit(...)
    local parts = {}
    for i = 1, select('#', ...) do parts[i] = __canon(select(i, ...)) end
    __log[#__log + 1] = '(' .. table.concat(parts, ' | ') .. ')'
end
local __ok, __err = pcall(function()
'''

HARNESS_POST = r'''
end)
collectgarbage('collect')
collectgarbage('collect')
return table.concat(__log, '\n') .. '\nok=' .. tostring(__ok) .. ' err=' .. __canon(__err)
'''

_ERR_PREFIX = re.compile(r'^(?:[^\n:]*:\d+: )+')
# Error position info (chunkname:line:) is a source-layout artifact that cannot
# survive transformation; documented NON-SEMANTIC boundary (see docs/).
_POS_TOKEN = re.compile(r'\[string "<python>"\]:\d+: ')
# Identifier names and local/global qualifiers in error messages are
# renaming artifacts (NON-SEMANTIC, like positions). Canonicalize them.
_ERR_IDENT = re.compile(
    r"(attempt to (?:call|index)) \w+ '[^']*'")
_ERR_ARG = re.compile(r"bad argument #(\d+) to '[^']*'")


def normalize(trace):
    """Normalize error position prefixes in the trailing err= field only."""
    head, sep, tail = trace.rpartition(' err=')
    if not sep:
        return trace
    m = re.match(r'(s:\d+:)(.*)', tail, re.DOTALL)
    if m:
        trace = head + sep + m.group(1) + _ERR_PREFIX.sub('', m.group(2))
    trace = _POS_TOKEN.sub('', trace)
    # length prefixes in the canon predate position stripping
    trace = _ERR_IDENT.sub(r"\1 <var> '<name>'", trace)
    trace = _ERR_ARG.sub(r"bad argument #\1 to '<fn>'", trace)
    return re.sub(r's:\d+:', 's:', trace)


def run_chunk(source):
    lua = LuaRuntime(unpack_returned_tuples=True)
    try:
        out = lua.execute(source)
    except Exception as e:  # harness/generator bug, not a semantic mismatch
        return 'HARNESS_ERROR: %s' % e
    return normalize(str(out))

# ------------------------------------------------------------- fixed probes --

PROBES = {
    'tail_call_deep': '''
        local function loop(n, acc)
            if n == 0 then return acc end
            return loop(n - 1, acc + 1)
        end
        __emit(loop(20000, 0))''',
    'tail_call_mutual_deep': '''
        local a, b
        function a(n, acc) if n == 0 then return acc end return b(n - 1, acc + 1) end
        function b(n, acc) return a(n - 1, acc + 2) end
        __emit(a(15000, 0))''',
    'gc_closure_retention': '''
        local fs = {}
        for i = 1, 300 do
            local x = i
            fs[i] = function() return x * 2 end
            if i % 3 == 0 then fs[i] = nil end
            if i % 50 == 0 then collectgarbage('collect') end
        end
        collectgarbage('collect'); collectgarbage('collect')
        local s = 0
        for i = 1, 300 do if fs[i] then s = s + fs[i]() end end
        __emit(s)''',
    'gc_upvalue_writeback': '''
        local acc = 0
        local writers = {}
        for i = 1, 100 do
            local d = i
            writers[i] = function() acc = acc + d end
        end
        collectgarbage('collect')
        for i = 1, 100 do writers[i]() end
        collectgarbage('collect'); collectgarbage('collect')
        __emit(acc)''',
    'gc_finalizer_flags': '''
        local fired = {}
        for i = 1, 5 do
            local p = newproxy(true)
            getmetatable(p).__gc = function() fired[i] = true end
            p = nil
        end
        collectgarbage('collect'); collectgarbage('collect')
        local n = 0
        for i = 1, 5 do if fired[i] then n = n + 1 end end
        __emit(n)''',
    'coroutine_nested_yield': '''
        local function work(k)
            local t = 0
            for i = 1, 3 do
                t = t + coroutine.yield(k .. ':' .. i, i * k)
            end
            return t, 'done'
        end
        local co = coroutine.create(work)
        __emit(coroutine.resume(co, 10))
        __emit(coroutine.resume(co, 1))
        __emit(coroutine.resume(co, 2))
        __emit(coroutine.resume(co, 3))
        __emit(coroutine.status(co))''',
    'coroutine_error_propagation': '''
        local co = coroutine.create(function()
            coroutine.yield('first')
            error('boom', 0)
        end)
        __emit(coroutine.resume(co))
        __emit(coroutine.resume(co))
        __emit(coroutine.status(co))''',
    'error_levels_and_payloads': '''
        __emit(pcall(function() error('plain', 0) end))
        __emit(pcall(function() error({code = 5}) end))
        __emit(pcall(function() error(42) end))
        __emit(pcall(function() error('x') end))
        __emit(pcall(function() assert(false, 'assert-msg') end))
        __emit(pcall(function() assert(nil) end))
        __emit(pcall(error, 'via-arg', 0))''',
    'xpcall_handler': '''
        __emit(xpcall(function() error({tag='e'}) end,
                      function(m) return type(m), m and m.tag or 'none' end))
        __emit(xpcall(function() return 1, 2 end,
                      function(m) return 'unused' end))''',
    'global_env_ops': '''
        RYONEX_FUZZ_G = (RYONEX_FUZZ_G or 0) + 5
        local g1 = RYONEX_FUZZ_G
        local function readg() return RYONEX_FUZZ_G end
        RYONEX_FUZZ_G = g1 * 2
        __emit(g1, readg(), RYONEX_FUZZ_G, RYONEX_FUZZ_MISSING)''',
    'vararg_trailing_nil': '''
        local function f(...)
            __emit(select('#', ...), ...)
            return ...
        end
        f(1, nil, nil)
        f()
        f(nil)
        local a, b, c, d = f(7, nil)
        __emit(a, b, c, d)''',
    'multi_adjust_contexts': '''
        local function f() return 1, nil, 3 end
        local t = {f()}
        __emit(#t, t[1], t[2], t[3])
        local u = {(f()), 9}
        __emit(#u, u[1], u[2])
        local function h(a, b, c, d) __emit(a, b, c, d) end
        h(f(), 99)
        h((f()))
        local function g(...) return select('#', ...) end
        __emit(g(f()))
        __emit(g((f())))''',
    'upvalue_shared_between_closures': '''
        local n = 0
        local function inc() n = n + 1 end
        local function get() return n end
        inc(); inc()
        __emit(get(), n)
        local function outer()
            local x = 1
            local function set(v) x = v end
            local function rd() return x end
            return set, rd
        end
        local s, r = outer()
        __emit(r())
        s(99)
        __emit(r())''',
}


class Gen:
    """Random Lua 5.1 program generator with a small value-kind discipline."""

    NUM_LITS = ['0', '1', '2', '3', '7', '10', '0.5', '1e10', '123456789012345678',
                '0x7fffffff', '3.14159265358979311599796346854', '1e-308', '1e308']
    STR_LITS = ["'hello'", "'a\\0b'", "'\\65\\66\\67'", "'x y'", "''", "'\\n\\t'",
                "'...'", "'\\\\'", "'%d-%s'"]

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.n = 0
        self.env = []      # (name, kind)
        self.funcs = []    # (name, ret_kind, multi, nparams, vararg)
        self.in_vararg = False

    def fresh(self, p='v'):
        self.n += 1
        return '%s%d' % (p, self.n)

    def gen_expr(self, kind, d):
        r = self.rng
        if kind == 'num':
            return self.gen_num(d)
        if kind == 'str':
            return self.gen_str(d)
        if kind == 'bool':
            return self.gen_bool(d)
        if kind == 'tbl':
            return self.gen_tbl(d)
        return self.gen_any(d)

    def _call(self, d, ret):
        cands = [f for f in self.funcs if f[1] == ret]
        if not cands or d <= 0:
            return None
        name, _ret, multi, nparams, vararg = self.rng.choice(cands)
        args = [self.gen_expr('any', d - 1) for _ in range(self.rng.randint(0, nparams))]
        if vararg and self.in_vararg and self.rng.random() < 0.4:
            args.append('...')
        return '%s(%s)' % (name, ', '.join(args))

    def gen_num(self, d):
        r = self.rng
        c = r.random()
        if d <= 0 or c < 0.32:
            return r.choice(self.NUM_LITS)
        if c < 0.42:
            vs = [n for n, k in self.env if k == 'num']
            return r.choice(vs) if vs else r.choice(self.NUM_LITS)
        if c < 0.58:
            return '(%s %s %s)' % (self.gen_num(d - 1),
                                   r.choice(['+', '-', '*', '/', '%', '^']),
                                   self.gen_num(d - 1))
        if c < 0.65:
            return '(-%s)' % self.gen_num(d - 1)
        if c < 0.73:
            return '#(%s)' % self.gen_str(d - 1)
        if c < 0.84:
            call = self._call(d - 1, 'num')
            return call if call else r.choice(self.NUM_LITS)
        if c < 0.92:
            return '(%s and %s or %s)' % (self.gen_bool(d - 1), self.gen_num(d - 1),
                                          self.gen_num(d - 1))
        return '(tonumber(%s) or 0)' % self.gen_expr('any', d - 1)

    def gen_str(self, d):
        r = self.rng
        c = r.random()
        if d <= 0 or c < 0.35:
            return r.choice(self.STR_LITS)
        if c < 0.5:
            vs = [n for n, k in self.env if k == 'str']
            return r.choice(vs) if vs else r.choice(self.STR_LITS)
        if c < 0.62:
            return '(%s .. %s)' % (self.gen_str(d - 1), self.gen_str(d - 1))
        if c < 0.72:
            return '(%s):sub(%d, %d)' % (self.gen_str(d - 1),
                                         self.rng.randint(-4, 1), self.rng.randint(1, 6))
        if c < 0.8:
            return 'string.rep(%s, %d)' % (self.gen_str(d - 1), self.rng.randint(0, 3))
        if c < 0.87:
            return 'string.format(%s, %s)' % (self.gen_str(d - 1), self.gen_num(d - 1))
        call = self._call(d - 1, 'str')
        return call if call else self.rng.choice(self.STR_LITS)



    def gen_bool(self, d):
        r = self.rng
        c = r.random()
        if d <= 0 or c < 0.3:
            return r.choice(['true', 'false'])
        if c < 0.5:
            k = r.choice(['num', 'str', 'bool'])
            return '(%s %s %s)' % (self.gen_expr(k, d - 1),
                                   r.choice(['<', '>', '<=', '>=', '==', '~=']),
                                   self.gen_expr(k, d - 1))
        if c < 0.62:
            return '(not %s)' % self.gen_bool(d - 1)
        if c < 0.78:
            return '(%s %s %s)' % (self.gen_bool(d - 1),
                                   r.choice(['and', 'or']), self.gen_bool(d - 1))
        call = self._call(d - 1, 'bool')
        return call if call else 'true'

    def gen_tbl(self, d):
        r = self.rng
        if d <= 0:
            return '{%s}' % ', '.join(self.gen_expr('any', 0)
                                      for _ in range(r.randint(0, 2)))
        fields = []
        for _ in range(r.randint(0, 3)):
            if r.random() < 0.3:
                fields.append('[%s] = %s' % (self.gen_expr(r.choice(['num', 'str']), d - 1),
                                             self.gen_expr('any', d - 1)))
            else:
                fields.append('%s = %s' % (self.fresh('k'), self.gen_expr('any', d - 1)))
        if r.random() < 0.35 and self.funcs:
            fields.append(self._call(d - 1, 'any') or 'nil')  # trailing multi expands
        return '{%s}' % ', '.join(fields)

    def gen_any(self, d):
        r = self.rng
        c = r.random()
        if d <= 0 or c < 0.3:
            return self.gen_expr(r.choice(['num', 'str', 'bool', 'tbl']), 0)
        if c < 0.42:
            vs = [n for n, k in self.env if k in ('any', 'num', 'str', 'bool', 'tbl')]
            return r.choice(vs) if vs else 'nil'
        if c < 0.54:
            call = self._call(d - 1, 'any')
            return call if call else 'nil'
        if c < 0.64:
            return '(%s)' % (self._call(d - 1, 'any') or 'nil')  # parens adjust to 1
        if c < 0.72:
            vs = [n for n, k in self.env if k == 'tbl']
            base = r.choice(vs) if vs else '{}'
            return '%s[%s]' % (base, self.gen_expr(r.choice(['num', 'str', 'any']), d - 1))
        if c < 0.78 and self.in_vararg:
            return r.choice(['...', '(...)'])
        if c < 0.88:
            return '(%s and %s or %s)' % (self.gen_bool(d - 1), self.gen_any(d - 1),
                                          self.gen_any(d - 1))
        return '(function() %s return %s end)()' % (
            self.gen_block(d - 1, 1), self.gen_expr('any', d - 1))

    def gen_multi(self, d):
        """A trailing multi-value expression (call, vararg or select)."""
        r = self.rng
        if (r.random() < 0.5 or not self.funcs) and self.funcs:
            name, _ret, _m, nparams, vararg = r.choice(self.funcs)
            args = [self.gen_expr('any', max(0, d - 1))
                    for _ in range(r.randint(0, nparams))]
            return '%s(%s)' % (name, ', '.join(args)), True
        if self.in_vararg:
            return '...', True
        return 'select("#", %s)' % self.gen_expr('any', max(0, d - 1)), True

    # -------------------------------------------------------- statements --
    def gen_block(self, d, nstmts, in_loop=False, in_fn=False):
        return ' '.join(self.gen_stmt(d, in_loop, in_fn) for _ in range(nstmts))

    def gen_function_def(self, d, ret_kind=None):
        r = self.rng
        name = self.fresh('fn')
        params = []
        for _ in range(r.randint(0, 3)):
            p = self.fresh('p')
            params.append(p)
            self.env.append((p, 'any'))
        vararg = r.random() < 0.4
        if vararg:
            params.append('...')
        saved = self.in_vararg
        if vararg:
            self.in_vararg = True
        body = self.gen_block(max(0, d - 1), r.randint(1, 3), in_fn=True)
        self.in_vararg = saved
        style = r.random()
        multi = False
        if style < 0.18:
            ret = ''
        elif vararg and style < 0.38:
            ret, multi = ' return ...', True
        elif style < 0.68:
            items = self.gen_exprlist(max(0, d - 1))
            multi = len(items) > 1 or any('(' != i[0] for i in items[-1:]) and False
            ret = ' return ' + ', '.join(items)
            multi = True  # calls / vararg tails may expand; conservative
        else:
            call, _ = self.gen_multi(max(0, d - 1))
            tail = ', ' + self.gen_expr('any', 0) if r.random() < 0.3 else ''
            ret, multi = ' return ' + call + tail, True
        src = 'local function %s(%s) %s %s end' % (name, ', '.join(params), body, ret)
        self.funcs.append((name, ret_kind or 'any', multi, len(params), vararg))
        return src

    def gen_stmt(self, d, in_loop, in_fn):
        r = self.rng
        c = r.random()
        if c < 0.16:  # local declaration
            names, kinds = [], []
            for _ in range(r.randint(1, 2)):
                nm = self.fresh('x')
                k = r.choice(['num', 'str', 'bool', 'tbl', 'any'])
                names.append(nm)
                kinds.append(k)
                self.env.append((nm, k))
            return 'local %s = %s' % (', '.join(names), ', '.join(self.gen_exprlist(d)))
        if c < 0.3:  # assignment (incl. multi-target padding)
            if self.env and r.random() < 0.6:
                targets = []
                for _ in range(r.randint(1, 2)):
                    nm, k = r.choice(self.env)
                    targets.append(nm)
                return '%s = %s' % (', '.join(targets), ', '.join(self.gen_exprlist(d)))
            t = self.fresh('t')
            self.env.append((t, 'tbl'))
            return 'local %s = %s; %s[%s] = %s' % (
                t, self.gen_tbl(d), t,
                self.gen_expr(r.choice(['num', 'str']), max(0, d - 1)),
                self.gen_expr('any', max(0, d - 1)))
        if c < 0.44:  # trace emission: core multi-value probe
            return '__emit(%s)' % ', '.join(self.gen_exprlist(d))
        if c < 0.52:  # call statement
            if self.funcs and r.random() < 0.7:
                return self._call(d, 'any') or '__emit(0)'
            return 'pcall(%s)' % (self._call(max(0, d - 1), 'any') or 'error')
        if c < 0.6 and d > 0:
            return 'if %s then %s else %s end' % (
                self.gen_bool(d - 1), self.gen_block(d - 1, 1, in_loop, in_fn),
                self.gen_block(d - 1, 1, in_loop, in_fn))
        if c < 0.68 and d > 0:
            i = self.fresh('i')
            self.env.append((i, 'num'))
            step = r.choice(['1', '2', '-1', '0.5'])
            return 'for %s = 1, %d, %s do %s end' % (
                i, r.randint(1, 6), step, self.gen_block(d - 1, 2, True, in_fn))
        if c < 0.74 and d > 0:
            i = self.fresh('i')
            self.env.append((i, 'num'))
            return 'local %s = 0; while %s < 5 do %s = %s + 1 %s end' % (
                i, i, i, i, self.gen_block(d - 1, 1, True, in_fn))
        if c < 0.79 and d > 0:
            return 'repeat %s until %s' % (self.gen_block(d - 1, 1, True, in_fn),
                                           self.gen_bool(d - 1))
        if c < 0.88 and d > 0:
            return self.gen_function_def(d - 1)
        if c < 0.93:  # error-propagation probe inside pcall
            payload = r.choice(["'e%d'" % self.rng.randint(1, 9),
                                '{code=%d}' % self.rng.randint(1, 9),
                                '%d' % self.rng.randint(1, 9)])
            return '__emit(pcall(function() error(%s, 0) end))' % payload
        return self.gen_template(d, in_loop, in_fn)

    def gen_exprlist(self, d):
        r = self.rng
        n = r.randint(1, 3)
        items = []
        for i in range(n):
            if i == n - 1 and r.random() < 0.4:
                code, _ = self.gen_multi(d)
                items.append(code)
            else:
                items.append(self.gen_expr(r.choice(['num', 'str', 'bool', 'any']), d))
        return items

    def gen_template(self, d, in_loop, in_fn):
        r = self.rng
        t = r.randint(0, 5)
        if t == 0:  # upvalue capture / writeback
            v, cl = self.fresh('u'), self.fresh('c')
            self.env.extend([(v, 'num'), (cl, 'any')])
            return ('local %s = %s; local %s = function(x) %s = %s + x return %s end; '
                    '__emit(%s(%s), %s)') % (
                v, self.gen_num(0), cl, v, v, v, cl, self.gen_num(0), v)
        if t == 1:  # closures in a loop (per-iteration upvalues)
            tbl, i = self.fresh('ct'), self.fresh('i')
            self.env.extend([(tbl, 'tbl'), (i, 'num')])
            return ('local %s = {}; for %s = 1, 4 do local x = %s; '
                    '%s[%s] = function() return x, %s end end; '
                    '__emit(%s[1]()); __emit(%s[4]())') % (
                tbl, i, i, tbl, i, i, tbl, tbl)
        if t == 2:  # coroutine resume/yield with multi values
            co = self.fresh('co')
            self.env.append((co, 'any'))
            return ('local %s = coroutine.create(function(a) '
                    'local y = coroutine.yield(a, a .. ":y") '
                    'return y, "done" end); '
                    '__emit(coroutine.resume(%s, %s)); '
                    '__emit(coroutine.resume(%s, %s))') % (
                co, co, self.gen_str(0), co, self.gen_num(0))
        if t == 3:  # metamethod op probe
            o1, o2, mt = self.fresh('o'), self.fresh('o'), self.fresh('mt')
            self.env.extend([(o1, 'tbl'), (o2, 'tbl')])
            op = r.choice(['__add', '__concat', '__call', '__index', '__tostring'])
            if op == '__add':
                body = '__emit(%s + %s)' % (o1, o2)
                mm = '__add = function(a, b) return a.v + b.v end,'
            elif op == '__concat':
                body = '__emit(%s .. %s)' % (o1, o2)
                mm = '__concat = function(a, b) return tostring(a.v) .. tostring(b.v) end,'
            elif op == '__call':
                body = '__emit(%s(%s))' % (o1, self.gen_num(0))
                mm = '__call = function(s, x) return s.v * x end,'
            elif op == '__tostring':
                body = '__emit(tostring(%s))' % o1
                mm = '__tostring = function(s) return "ob" .. s.v end,'
            else:
                body = '__emit(%s.miss)' % o1
                mm = '__index = function(t, k) return k .. "!" end,'
            return ('local %s = {%s __index = {kind = "b"}}; '
                    'local %s = setmetatable({v = %s}, %s); '
                    'local %s = setmetatable({v = %s}, %s); %s') % (
                mt, mm, o1, self.gen_num(0), mt, o2, self.gen_num(0), mt, body)
        if t == 4:  # GC interleave probe
            tbl, i = self.fresh('gt'), self.fresh('i')
            self.env.extend([(tbl, 'tbl'), (i, 'num')])
            return ('local %s = {}; for %s = 1, 20 do local x = %s * 2; '
                    '%s[%s] = function() return x end end; '
                    'collectgarbage("collect"); collectgarbage("collect"); '
                    'local s = 0; for j = 1, 20 do s = s + %s[j]() end; __emit(s)') % (
                tbl, i, i, tbl, i, tbl)
        # t == 5: bounded tail-recursive call
        fn = self.fresh('tr')
        self.env.append((fn, 'any'))
        depth = r.randint(50, 400)
        return ('local function %s(n, acc) if n == 0 then return acc end '
                'return %s(n - 1, acc + 1) end; __emit(%s(%d, 0))') % (
            fn, fn, fn, depth)


# ------------------------------------------------------------------ main --

def wrap(body):
    return HARNESS_PRE + body + HARNESS_POST


def diff_case(name, body, profiles, seed, keep=None):
    source = wrap(body)
    expected = run_chunk(source)
    failures = []
    for profile in profiles:
        try:
            protected = transform(source, profile, seed)
        except Exception as e:
            failures.append((profile, 'BUILD_ERROR: %s' % e, None))
            continue
        actual = run_chunk(protected)
        if actual != expected:
            failures.append((profile, expected, actual))
    if failures and keep:
        (Path(keep) / ('%s.lua' % name)).write_text(body)
    return expected, failures


def main():
    ap = argparse.ArgumentParser(description='RYONEX differential semantic fuzzer')
    ap.add_argument('--programs', type=int, default=25)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--profiles', default='balanced,hardened')
    ap.add_argument('--keep', default=None, help='directory for failing programs')
    ap.add_argument('--probes-only', action='store_true')
    args = ap.parse_args()
    profiles = args.profiles.split(',')
    if args.keep:
        Path(args.keep).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    mismatches = 0
    # 1. fixed probes
    for name in sorted(PROBES):
        expected, failures = diff_case('probe_' + name, PROBES[name], profiles,
                                       args.seed, args.keep)
        status = 'OK' if not failures else 'MISMATCH'
        print('probe %-28s %s' % (name, status))
        for profile, exp, act in failures:
            mismatches += 1
            print('  FAIL[%s] seed=%d' % (profile, args.seed))
            if act is not None:
                print('    expected: %s' % exp[:400])
                print('    actual:   %s' % act[:400])
            else:
                print('    %s' % exp)
    # 2. random programs
    if not args.probes_only:
        for i in range(args.programs):
            fseed = args.seed * 100003 + i
            g = Gen(fseed)
            for _ in range(g.rng.randint(1, 3)):
                g.gen_function_def(2, 'any')
            for _ in range(g.rng.randint(1, 3)):
                g.gen_function_def(2, 'num')
            body = g.gen_block(3, g.rng.randint(4, 9))
            name = 'fuzz_%d' % fseed
            expected, failures = diff_case(name, body, profiles, fseed, args.keep)
            if expected.startswith('HARNESS_ERROR'):
                print('gen %-12s INVALID-GENERATOR: %s' % (name, expected[:200]))
                mismatches += 1
                continue
            status = 'OK' if not failures else 'MISMATCH'
            print('fuzz %-12s %s' % (name, status))
            for profile, exp, act in failures:
                mismatches += 1
                print('  FAIL[%s] fseed=%d' % (profile, fseed))
                if act is not None:
                    print('    expected: %s' % exp[:400])
                    print('    actual:   %s' % act[:400])
                else:
                    print('    %s' % exp)
    print('\n%d programs+probes, %d mismatches, %.1fs' %
          (len(PROBES) + (0 if args.probes_only else args.programs),
           mismatches, time.time() - t0))
    return 1 if mismatches else 0


if __name__ == '__main__':
    sys.exit(main())
