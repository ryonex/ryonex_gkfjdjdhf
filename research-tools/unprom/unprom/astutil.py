"""Small shared helpers for pattern-matching over the AST."""

from __future__ import annotations

from typing import Optional

from . import ast_nodes as A
from .evaluator import const_value


def unparen(node: A.Node) -> A.Node:
    while isinstance(node, A.Paren):
        node = node.inner
    return node


def as_number(node: A.Node) -> Optional[float]:
    ok, v = const_value(node)
    if ok and isinstance(v, float):
        return v
    return None


def as_int(node: A.Node) -> Optional[int]:
    v = as_number(node)
    if v is None:
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if abs(v - round(v)) < 1e-9:
        return int(round(v))
    return None


def as_string(node: A.Node) -> Optional[str]:
    ok, v = const_value(node)
    if ok and isinstance(v, str):
        return v
    return None


def name_of(node: A.Node) -> Optional[str]:
    node = unparen(node)
    return node.name if isinstance(node, A.Name) else None


def binding_of(node: A.Node) -> Optional[int]:
    node = unparen(node)
    return node.binding if isinstance(node, A.Name) else None


def is_call_of(node: A.Node, binding: Optional[int] = None,
               name: Optional[str] = None) -> bool:
    node = unparen(node)
    if not isinstance(node, A.Call):
        return False
    f = unparen(node.func)
    if not isinstance(f, A.Name):
        return False
    if binding is not None and f.binding != binding:
        return False
    if name is not None and f.name != name:
        return False
    return True


def contains_number(node: A.Node, value: float) -> bool:
    for n in A.walk(node):
        if isinstance(n, A.Number) and n.value == value:
            return True
    return False


def contains_string(node: A.Node, needle: str) -> bool:
    for n in A.walk(node):
        if isinstance(n, A.String) and needle in n.value:
            return True
    return False


def count_binding_uses(chunk: A.Node, binding: int) -> int:
    return sum(1 for n in A.walk(chunk)
              if isinstance(n, A.Name) and n.binding == binding)
