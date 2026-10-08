"""AST node definitions plus generic visitor / transformer machinery.

The tree is intentionally small and uniform.  Every node is a dataclass that
inherits from :class:`Node`; child links are either ``Node`` instances or lists
of ``Node``.  ``iter_fields`` / ``NodeTransformer`` walk those links generically
so passes never have to enumerate every node type.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, List, Optional, Tuple


class Node:
    # Populated by dataclass subclasses; declared here for typing only.
    line: int = 0

    @property
    def kind(self) -> str:
        return type(self).__name__


def iter_fields(node: Node):
    for f in fields(node):
        if f.name in ("line", "col"):
            continue
        yield f.name, getattr(node, f.name)


def iter_child_nodes(node: Node):
    for _name, value in iter_fields(node):
        if isinstance(value, Node):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    yield item
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            yield sub


def walk(node: Node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(list(iter_child_nodes(cur)))


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------
@dataclass
class Chunk(Node):
    body: "Block"
    line: int = 0


@dataclass
class Block(Node):
    stmts: List[Node] = field(default_factory=list)
    line: int = 0


@dataclass
class LocalAssign(Node):
    targets: List["Name"] = field(default_factory=list)
    values: List[Node] = field(default_factory=list)
    attribs: List[Optional[str]] = field(default_factory=list)
    line: int = 0


@dataclass
class Assign(Node):
    targets: List[Node] = field(default_factory=list)
    values: List[Node] = field(default_factory=list)
    line: int = 0


@dataclass
class CompoundAssign(Node):
    op: str = "+"          # one of + - * / % ^ ..
    target: Node = None
    value: Node = None
    line: int = 0


@dataclass
class CallStat(Node):
    call: Node = None
    line: int = 0


@dataclass
class Do(Node):
    body: Block = None
    line: int = 0


@dataclass
class While(Node):
    cond: Node = None
    body: Block = None
    line: int = 0


@dataclass
class Repeat(Node):
    body: Block = None
    cond: Node = None
    line: int = 0


@dataclass
class If(Node):
    # list of (condition, Block)
    clauses: List[Tuple[Node, Block]] = field(default_factory=list)
    orelse: Optional[Block] = None
    line: int = 0


@dataclass
class NumericFor(Node):
    var: "Name" = None
    start: Node = None
    stop: Node = None
    step: Optional[Node] = None
    body: Block = None
    line: int = 0


@dataclass
class GenericFor(Node):
    names: List["Name"] = field(default_factory=list)
    exprs: List[Node] = field(default_factory=list)
    body: Block = None
    line: int = 0


@dataclass
class FunctionDecl(Node):
    # target is a Name / Index chain; is_method True => trailing ':name'
    target: Node = None
    is_method: bool = False
    func: "Function" = None
    line: int = 0


@dataclass
class LocalFunction(Node):
    name: "Name" = None
    func: "Function" = None
    line: int = 0


@dataclass
class Return(Node):
    values: List[Node] = field(default_factory=list)
    line: int = 0


@dataclass
class Break(Node):
    line: int = 0


@dataclass
class Continue(Node):
    line: int = 0


@dataclass
class Goto(Node):
    label: str = ""
    line: int = 0


@dataclass
class Label(Node):
    name: str = ""
    line: int = 0


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------
@dataclass
class Nil(Node):
    line: int = 0


@dataclass
class TrueExpr(Node):
    line: int = 0


@dataclass
class FalseExpr(Node):
    line: int = 0


@dataclass
class Vararg(Node):
    line: int = 0


@dataclass
class Number(Node):
    value: float = 0.0
    raw: str = ""
    line: int = 0


@dataclass
class String(Node):
    value: str = ""
    raw: Optional[str] = None       # original spelling if known
    line: int = 0


@dataclass
class Name(Node):
    name: str = ""
    # filled in by the resolver: a stable id for the binding this refers to
    binding: Optional[int] = None
    line: int = 0


@dataclass
class Index(Node):
    obj: Node = None
    key: Node = None
    # ``dot`` is a formatting hint: True means it was written ``a.b``.
    dot: bool = False
    line: int = 0


@dataclass
class Call(Node):
    func: Node = None
    args: List[Node] = field(default_factory=list)
    line: int = 0


@dataclass
class MethodCall(Node):
    obj: Node = None
    method: str = ""
    args: List[Node] = field(default_factory=list)
    line: int = 0


@dataclass
class Function(Node):
    params: List["Name"] = field(default_factory=list)
    is_vararg: bool = False
    body: Block = None
    line: int = 0


@dataclass
class Table(Node):
    # each field: ("pos", None, value) | ("name", "key", value) | ("expr", keyNode, value)
    fields: List[Tuple[str, Any, Node]] = field(default_factory=list)
    line: int = 0


@dataclass
class BinOp(Node):
    op: str = ""
    left: Node = None
    right: Node = None
    line: int = 0


@dataclass
class UnOp(Node):
    op: str = ""
    operand: Node = None
    line: int = 0


@dataclass
class Paren(Node):
    inner: Node = None
    line: int = 0


# --------------------------------------------------------------------------
# Transformer
# --------------------------------------------------------------------------
class NodeTransformer:
    """Mutating visitor.  ``visit_<Kind>`` may return a replacement node,
    ``None`` to drop a statement (only valid inside a statement list), or the
    node itself.  Everything else is handled generically."""

    def visit(self, node: Node) -> Any:
        method = getattr(self, "visit_" + node.kind, None)
        if method is not None:
            return method(node)
        return self.generic_visit(node)

    def generic_visit(self, node: Node) -> Node:
        for name, value in list(iter_fields(node)):
            if isinstance(value, Node):
                new = self.visit(value)
                setattr(node, name, new)
            elif isinstance(value, list):
                setattr(node, name, self._visit_list(value))
        return node

    def _visit_list(self, lst: list) -> list:
        out = []
        for item in lst:
            if isinstance(item, Node):
                res = self.visit(item)
                if res is None:
                    continue
                if isinstance(res, list):
                    out.extend(res)
                else:
                    out.append(res)
            elif isinstance(item, tuple):
                out.append(tuple(self.visit(s) if isinstance(s, Node) else s
                                 for s in item))
            else:
                out.append(item)
        return out


class NodeVisitor:
    """Read-only walk."""

    def visit(self, node: Node) -> Any:
        method = getattr(self, "visit_" + node.kind, None)
        if method is not None:
            return method(node)
        return self.generic_visit(node)

    def generic_visit(self, node: Node):
        for child in iter_child_nodes(node):
            self.visit(child)


def clone(node: Any) -> Any:
    """Deep copy of a node tree (bindings preserved)."""
    if isinstance(node, Node):
        kwargs = {}
        for name, value in iter_fields(node):
            kwargs[name] = clone(value)
        new = type(node)(**kwargs)
        new.line = getattr(node, "line", 0)
        return new
    if isinstance(node, list):
        return [clone(x) for x in node]
    if isinstance(node, tuple):
        return tuple(clone(x) for x in node)
    return node
