"""Reverse ``ProxifyLocals`` (best effort).

ProxifyLocals is not part of any built-in preset, so this pass targets the
default ``LiteralType = "string"`` configuration and the straightforward
declaration / read / write shapes it produces::

    local e = function() end
    local x = setmetatable({ [VN] = <init> },
                           { __opG = function(s) return s[VN] end,
                             __opS = function(s, a) s[VN] = a end })
    ...  x <opG> "<junk>"      -- a read of x
    ...  e(x <opS> <value>)    -- a write  x = <value>
    ...  x[VN] = <value>       -- also a write

Anything that does not match is left untouched.
"""

from __future__ import annotations

from typing import Dict, Optional

from .. import ast_nodes as A
from ..astutil import binding_of, name_of, unparen
from ..scope import resolve

_META_OP = {
    "__add": "+", "__sub": "-", "__mul": "*", "__div": "/",
    "__pow": "^", "__concat": "..",
}


class _Proxy:
    __slots__ = ("value_name", "get_op", "set_op", "get_index", "set_index")

    def __init__(self):
        self.value_name = None
        self.get_op = None       # operator string, or None when get uses indexing
        self.set_op = None
        self.get_index = False
        self.set_index = False


def _table_fields(t: A.Table):
    out = {}
    for kind, key, val in t.fields:
        if kind == "name":
            out[key] = val
        elif kind == "expr":
            k = key.value if isinstance(key, A.String) else None
            if k is not None:
                out[k] = val
        elif kind == "pos":
            out.setdefault("__pos__", []).append(val)
    return out


def _analyse_decl(node: A.LocalAssign) -> Optional[tuple]:
    if len(node.targets) != 1 or len(node.values) != 1:
        return None
    call = unparen(node.values[0])
    if not (isinstance(call, A.Call) and name_of(call.func) == "setmetatable"
            and len(call.args) == 2):
        return None
    obj, meta = unparen(call.args[0]), unparen(call.args[1])
    if not (isinstance(obj, A.Table) and isinstance(meta, A.Table)):
        return None
    if len(obj.fields) != 1:
        return None
    okind, okey, oinit = obj.fields[0]
    vn = okey if isinstance(okey, str) else (okey.value if isinstance(okey, A.String) else None)
    if vn is None:
        return None
    mf = _table_fields(meta)
    px = _Proxy()
    px.value_name = vn
    for mkey, fn in mf.items():
        fn = unparen(fn)
        if not isinstance(fn, A.Function):
            continue
        is_getter = _fn_returns_member(fn, vn)
        is_setter = _fn_assigns_member(fn, vn)
        if is_getter:
            if mkey == "__index":
                px.get_index = True
            else:
                px.get_op = _META_OP.get(mkey)
        elif is_setter:
            if mkey == "__index" or mkey == "__newindex":
                px.set_index = True
            else:
                px.set_op = _META_OP.get(mkey)
    if px.get_op is None and not px.get_index:
        return None
    return node.targets[0].binding, px, oinit


def _fn_returns_member(fn: A.Function, vn: str) -> bool:
    for st in A.walk(fn.body):
        if isinstance(st, A.Return) and len(st.values) == 1:
            v = unparen(st.values[0])
            if isinstance(v, A.Index) and _is_member(v, vn):
                return True
            if isinstance(v, A.Call) and name_of(v.func) == "rawget" \
                    and len(v.args) == 2 and _str_eq(v.args[1], vn):
                return True
    return False


def _fn_assigns_member(fn: A.Function, vn: str) -> bool:
    for st in A.walk(fn.body):
        if isinstance(st, A.Assign) and len(st.targets) == 1:
            if isinstance(unparen(st.targets[0]), A.Index) \
                    and _is_member(unparen(st.targets[0]), vn):
                return True
    return False


def _is_member(idx: A.Index, vn: str) -> bool:
    return _str_eq(idx.key, vn)


def _str_eq(node: A.Node, s: str) -> bool:
    node = unparen(node)
    return isinstance(node, A.String) and node.value == s


class _Rewrite(A.NodeTransformer):
    def __init__(self, proxies: Dict[int, _Proxy], empty_binding, ctx):
        self.px = proxies
        self.empty = empty_binding
        self.ctx = ctx

    def visit_CallStat(self, node: A.CallStat):
        node = self.generic_visit(node)
        call = unparen(node.call)
        if isinstance(call, A.Call) and binding_of(call.func) == self.empty \
                and len(call.args) == 1:
            inner = unparen(call.args[0])
            if isinstance(inner, A.BinOp):
                b = binding_of(inner.left)
                px = self.px.get(b)
                if px and inner.op == px.set_op:
                    self.ctx.bump("proxify_writes")
                    return A.Assign(targets=[A.Name(name=name_of(inner.left),
                                                    binding=b)],
                                    values=[inner.right], line=node.line)
        return node

    def visit_Assign(self, node: A.Assign):
        node = self.generic_visit(node)
        if len(node.targets) == 1:
            t = unparen(node.targets[0])
            if isinstance(t, A.Index):
                b = binding_of(t.obj)
                px = self.px.get(b)
                if px and _str_eq(t.key, px.value_name):
                    self.ctx.bump("proxify_writes")
                    node.targets = [t.obj]
        return node

    def visit_BinOp(self, node: A.BinOp):
        node = self.generic_visit(node)
        b = binding_of(node.left)
        px = self.px.get(b)
        if px and px.get_op and node.op == px.get_op:
            self.ctx.bump("proxify_reads")
            return node.left
        return node

    def visit_Index(self, node: A.Index):
        node = self.generic_visit(node)
        b = binding_of(node.obj)
        px = self.px.get(b)
        if px and px.get_index and not _str_eq(node.key, px.value_name):
            # a read via  x[<junk>]
            self.ctx.bump("proxify_reads")
            return node.obj
        return node


def _find_empty_fn(chunk: A.Chunk) -> Optional[int]:
    for st in chunk.body.stmts:
        if isinstance(st, A.LocalAssign) and len(st.targets) == 1 \
                and len(st.values) == 1:
            v = unparen(st.values[0])
            if isinstance(v, A.Function) and not v.body.stmts and not v.params:
                return st.targets[0].binding
    return None


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    resolve(chunk)
    proxies: Dict[int, _Proxy] = {}
    inits = {}
    for n in A.walk(chunk):
        if isinstance(n, A.LocalAssign):
            res = _analyse_decl(n)
            if res:
                bid, px, init = res
                proxies[bid] = px
                inits[bid] = (n, init)
    if not proxies:
        return chunk
    empty_binding = _find_empty_fn(chunk)
    chunk = _Rewrite(proxies, empty_binding, ctx).visit(chunk)

    # rewrite the declarations themselves:  local x = <init>
    for bid, (decl, init) in inits.items():
        decl.values = [init]
    ctx.note(f"[proxify_locals] unwrapped {len(proxies)} proxied local(s)")
    return chunk
