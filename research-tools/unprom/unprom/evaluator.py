"""Constant evaluation of pure Lua expressions.

``const_value(node)`` returns ``(True, value)`` when ``node`` provably evaluates
to a Lua constant with no side effects, else ``(False, None)``.  Lua values map
to Python as: number->float, string->str, boolean->bool, nil->``LuaNil``.
"""

from __future__ import annotations

import math
from typing import Any, Tuple

from . import ast_nodes as A


class _Nil:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self):
        return "nil"


LuaNil = _Nil()

NOCONST: Tuple[bool, Any] = (False, None)


def is_truthy(v: Any) -> bool:
    return not (v is LuaNil or v is False)


def _lua_num_to_str(x: float) -> str:
    if x != x:
        return "nan"
    if x == math.inf:
        return "inf"
    if x == -math.inf:
        return "-inf"
    if x == int(x) and abs(x) < 1e16:
        return "%d" % int(x)
    return repr(x)


def _tonum(v: Any):
    if isinstance(v, bool):
        return None
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            if s[:2].lower() == "0x":
                return float(int(s, 16))
            return float(s)
        except ValueError:
            return None
    return None


def const_value(node: A.Node) -> Tuple[bool, Any]:
    k = node.kind
    if k == "Number":
        return True, float(node.value)
    if k == "String":
        return True, node.value
    if k == "TrueExpr":
        return True, True
    if k == "FalseExpr":
        return True, False
    if k == "Nil":
        return True, LuaNil
    if k == "Paren":
        return const_value(node.inner)
    if k == "UnOp":
        ok, v = const_value(node.operand)
        if not ok:
            return NOCONST
        if node.op == "-":
            n = _tonum(v)
            return (True, -n) if n is not None else NOCONST
        if node.op == "not":
            return True, not is_truthy(v)
        if node.op == "#":
            if isinstance(v, str):
                return True, float(len(v.encode("utf-8", "surrogatepass")))
            return NOCONST
        return NOCONST
    if k == "BinOp":
        op = node.op
        if op == "and":
            ok, l = const_value(node.left)
            if not ok:
                return NOCONST
            if not is_truthy(l):
                return True, l
            return const_value(node.right)
        if op == "or":
            ok, l = const_value(node.left)
            if not ok:
                return NOCONST
            if is_truthy(l):
                return True, l
            return const_value(node.right)
        ok, l = const_value(node.left)
        if not ok:
            return NOCONST
        ok, r = const_value(node.right)
        if not ok:
            return NOCONST
        return _bin(op, l, r)
    return NOCONST


def _bin(op: str, l: Any, r: Any) -> Tuple[bool, Any]:
    if op == "..":
        ls = l if isinstance(l, str) else (_lua_num_to_str(l) if isinstance(l, float) else None)
        rs = r if isinstance(r, str) else (_lua_num_to_str(r) if isinstance(r, float) else None)
        if ls is None or rs is None:
            return NOCONST
        return True, ls + rs
    if op == "==":
        return True, _lua_eq(l, r)
    if op == "~=":
        return True, not _lua_eq(l, r)
    if op in ("<", "<=", ">", ">="):
        if isinstance(l, str) and isinstance(r, str):
            a, b = l, r
        else:
            a, b = _tonum(l), _tonum(r)
            if a is None or b is None:
                return NOCONST
        return True, {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    a, b = _tonum(l), _tonum(r)
    if a is None or b is None:
        return NOCONST
    try:
        if op == "+":
            return True, a + b
        if op == "-":
            return True, a - b
        if op == "*":
            return True, a * b
        if op == "/":
            return True, a / b if b != 0 else math.copysign(math.inf, a) if a else math.nan
        if op == "%":
            if b == 0:
                return True, math.nan
            return True, a - math.floor(a / b) * b
        if op == "^":
            return True, float(a ** b)
        if op == "//":
            if b == 0:
                return True, math.copysign(math.inf, a) if a else math.nan
            return True, float(math.floor(a / b))
    except (OverflowError, ValueError):
        return NOCONST
    return NOCONST


def _lua_eq(l: Any, r: Any) -> bool:
    if isinstance(l, bool) or isinstance(r, bool):
        return l is r
    if l is LuaNil or r is LuaNil:
        return l is r
    if isinstance(l, float) and isinstance(r, float):
        return l == r
    if isinstance(l, str) and isinstance(r, str):
        return l == r
    return False


def value_to_node(v: Any) -> A.Node:
    if isinstance(v, bool):
        return A.TrueExpr() if v else A.FalseExpr()
    if v is LuaNil:
        return A.Nil()
    if isinstance(v, float):
        return A.Number(value=v, raw=_lua_num_to_str(v))
    if isinstance(v, str):
        return A.String(value=v)
    raise TypeError(f"cannot lift {v!r}")
