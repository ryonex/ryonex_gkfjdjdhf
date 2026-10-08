"""Reverse ``ConstantArray``.

Handles the common configuration (global wrapper function, optional array
rotation, optional base64 string encoding).  Local wrapper tables
(``LocalWrapperCount > 0``) are not yet undone -- a note is emitted if they are
detected.

Obfuscated shape::

    local ARR = { <b64 or raw literals> }
    for i, v in ipairs({{1, LEN}, {1, SHIFT}, {SHIFT + 1, LEN}}) do ... end   -- optional
    local function W(a) return ARR[a - OFFSET] end
    do <base64 decode loop over ARR> end                                     -- optional
    ...  W(<index>)  ...   -- every extracted constant
"""

from __future__ import annotations

from typing import List, Optional

from .. import ast_nodes as A
from ..astutil import as_int, as_string, binding_of, name_of, unparen
from ..evaluator import const_value
from ..scope import resolve


# -- structural finders ---------------------------------------------------
def _find_const_array(chunk: A.Chunk):
    for st in chunk.body.stmts:
        if not (isinstance(st, A.LocalAssign) and len(st.targets) == 1
                and len(st.values) == 1):
            continue
        tbl = unparen(st.values[0])
        if not isinstance(tbl, A.Table) or not tbl.fields:
            continue
        vals = []
        ok = True
        for kind, _key, v in tbl.fields:
            if kind != "pos":
                ok = False
                break
            cok, cv = const_value(v)
            if not cok:
                ok = False
                break
            vals.append(cv)
        if ok and len(vals) >= 2:
            return st, st.targets[0].binding, vals
    return None


def _find_wrapper(chunk: A.Chunk, arr_bind: int):
    def match_fn(fn: A.Function):
        if len(fn.params) < 1 or not fn.body.stmts:
            return None
        body = [s for s in fn.body.stmts]
        if len(body) != 1 or not isinstance(body[0], A.Return) \
                or len(body[0].values) != 1:
            return None
        idx = unparen(body[0].values[0])
        if not isinstance(idx, A.Index) or binding_of(idx.obj) != arr_bind:
            return None
        expr = unparen(idx.key)
        if not isinstance(expr, A.BinOp) or expr.op not in ("+", "-"):
            return None
        p0 = fn.params[0].binding
        off = as_int(expr.right)
        if binding_of(expr.left) != p0 or off is None:
            return None
        return -off if expr.op == "-" else off

    for st in chunk.body.stmts:
        if isinstance(st, A.LocalFunction):
            off = match_fn(st.func)
            if off is not None:
                return st, st.name.binding, off
        if isinstance(st, A.LocalAssign) and len(st.targets) == 1 \
                and len(st.values) == 1 and isinstance(unparen(st.values[0]), A.Function):
            off = match_fn(unparen(st.values[0]))
            if off is not None:
                return st, st.targets[0].binding, off
    return None


def _find_rotate_loop(chunk: A.Chunk, arr_bind: int):
    for st in chunk.body.stmts:
        if not isinstance(st, A.GenericFor) or len(st.exprs) != 1:
            continue
        call = unparen(st.exprs[0])
        if not (isinstance(call, A.Call) and name_of(call.func) == "ipairs"
                and len(call.args) == 1):
            continue
        outer = unparen(call.args[0])
        if not isinstance(outer, A.Table) or len(outer.fields) != 3:
            continue
        pairs = []
        for kind, _k, v in outer.fields:
            v = unparen(v)
            if kind != "pos" or not isinstance(v, A.Table) or len(v.fields) != 2:
                pairs = None
                break
            a = as_int(v.fields[0][2])
            b = as_int(v.fields[1][2])
            pairs.append((a, b))
        if not pairs:
            continue
        # {{1, LEN}, {1, SHIFT}, {SHIFT+1, LEN}}
        (a1, length), (a2, shift), (a3, length3) = pairs
        if a1 == 1 and a2 == 1 and shift is not None and length is not None \
                and a3 == shift + 1 and length3 == length \
                and _mentions_binding(st.body, arr_bind):
            return st, shift, length
    return None


def _find_decode_block(chunk: A.Chunk, arr_bind: int):
    for st in chunk.body.stmts:
        if not isinstance(st, A.Do):
            continue
        if not _mentions_binding(st, arr_bind):
            continue
        # the base64 loop divides by 65536 / raises 64 ^ (3 - k); those magic
        # numbers may still be obfuscated arithmetic, so evaluate.
        nums = set()
        for n in A.walk(st):
            if isinstance(n, (A.Number, A.BinOp, A.UnOp)):
                v = as_int(n)
                if v is not None:
                    nums.add(v)
        if 65536 not in nums and 64 not in nums:
            continue
        alphabet = _extract_alphabet(st)
        if alphabet:
            return st, alphabet
        return st, None          # decode block present but alphabet unreadable
    return None


def _has_decode_block(chunk: A.Chunk, arr_bind: int) -> bool:
    for st in chunk.body.stmts:
        if isinstance(st, A.Do) and _mentions_binding(st, arr_bind):
            for n in A.walk(st):
                if isinstance(n, A.Name) and n.name in (
                        "char", "byte", "sub", "gmatch") :
                    return True
            txt = {as_string(x) for x in A.walk(st) if isinstance(x, A.String)}
            if any(t == "=" for t in txt):        # base64 padding check
                return True
    return False


def _extract_alphabet(node: A.Node) -> Optional[str]:
    for n in A.walk(node):
        if not isinstance(n, A.Table) or len(n.fields) < 32:
            continue
        pos = {}
        ok = True
        for kind, key, val in n.fields:
            k = as_string(key) if isinstance(key, A.Node) else (
                key if isinstance(key, str) else None)
            iv = as_int(val)
            if k is None or len(k) != 1 or iv is None:
                ok = False
                break
            pos[iv] = k
        if ok and len(pos) >= 64 and set(range(64)) <= set(pos):
            return "".join(pos[i] for i in range(64))
    return None


def _mentions_binding(node: A.Node, binding: int) -> bool:
    return any(isinstance(n, A.Name) and n.binding == binding for n in A.walk(node))


# -- data transforms ----------------------------------------------------
def _reverse(t: List, i: int, j: int):
    while i < j:
        t[i], t[j] = t[j], t[i]
        i += 1
        j -= 1


def _rotate(t: List, shift: int):
    n = len(t)
    if n == 0:
        return
    shift %= n
    _reverse(t, 0, n - 1)
    _reverse(t, 0, shift - 1)
    _reverse(t, shift, n - 1)


def _b64decode(data: str, alphabet: str) -> str:
    lookup = {c: i for i, c in enumerate(alphabet)}
    out = bytearray()
    value = 0
    count = 0
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        code = lookup.get(ch)
        if code is not None:
            value += code * (64 ** (3 - count))
            count += 1
            if count == 4:
                count = 0
                out.append(value // 65536)
                out.append(value % 65536 // 256)
                out.append(value % 256)
                value = 0
        elif ch == "=":
            out.append(value // 65536)
            if i + 1 >= n or data[i + 1] != "=":
                out.append(value % 65536 // 256)
            break
        i += 1
    return out.decode("latin-1")


# -- main ---------------------------------------------------------------
class _Rewrite(A.NodeTransformer):
    def __init__(self, w_bind, off, values, ctx):
        self.w_bind = w_bind
        self.off = off
        self.values = values
        self.ctx = ctx

    def visit_Call(self, node: A.Call):
        node = self.generic_visit(node)
        if binding_of(node.func) != self.w_bind or len(node.args) != 1:
            return node
        arg = as_int(node.args[0])
        if arg is None:
            return node
        real = arg + self.off            # off is signed: ARR[a + off]
        idx = real - 1
        if 0 <= idx < len(self.values):
            v = self.values[idx]
            self.ctx.bump("constants_inlined")
            if isinstance(v, str):
                return A.String(value=v, line=node.line)
            if isinstance(v, float):
                return A.Number(value=v, line=node.line)
            if isinstance(v, bool):
                return A.TrueExpr() if v else A.FalseExpr()
        return node


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    resolve(chunk)
    found = _find_const_array(chunk)
    if not found:
        return chunk
    arr_stmt, arr_bind, values = found
    wrap = _find_wrapper(chunk, arr_bind)
    if not wrap:
        ctx.note("[constant_array] found array but no wrapper function; skipped")
        return chunk
    wrap_stmt, w_bind, off = wrap

    rot = _find_rotate_loop(chunk, arr_bind)
    dec = _find_decode_block(chunk, arr_bind)

    # If the array is base64-encoded but we can't read the alphabet, inlining
    # would produce garbage strings - leave the whole thing intact instead.
    if dec and dec[1] is None:
        ctx.note("[constant_array] base64 alphabet unreadable; leaving array, "
                 "decoder and wrapper in place")
        return chunk
    if not dec and _has_decode_block(chunk, arr_bind):
        ctx.note("[constant_array] a decoder block was detected but not matched; "
                 "leaving array, decoder and wrapper in place")
        return chunk

    if rot:
        _rotate(values, rot[1])
        ctx.note(f"[constant_array] replayed rotation shift={rot[1]}")
    if dec:
        values = [_b64decode(v, dec[1]) if isinstance(v, str) else v
                  for v in values]
        ctx.note("[constant_array] base64-decoded array entries")

    chunk = _Rewrite(w_bind, off, values, ctx).visit(chunk)

    remaining = sum(
        1 for n in A.walk(chunk)
        if isinstance(n, A.Call) and binding_of(n.func) == w_bind)
    drop = {id(arr_stmt), id(wrap_stmt)}
    if rot:
        drop.add(id(rot[0]))
    if dec:
        drop.add(id(dec[0]))
    if remaining > 0:
        ctx.note(f"[constant_array] {remaining} wrapper call(s) unresolved; "
                 "keeping array/wrapper")
        drop = set()
    chunk.body.stmts = [s for s in chunk.body.stmts if id(s) not in drop]
    if drop:
        ctx.note("[constant_array] removed array, wrapper and setup code")
    return chunk
