"""Lexical scope / binding resolution.

After :func:`resolve`, every :class:`ast_nodes.Name` that refers to a local has
``name.binding`` set to a small integer id, and ``binding`` is ``None`` for a
free (global) reference.  ``Resolver.bindings`` maps id -> :class:`Binding` with
the definition sites and use sites, which the rename / substitution passes rely
on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ast_nodes as A


@dataclass
class Binding:
    id: int
    name: str
    kind: str                      # 'local' | 'param' | 'loop' | 'localfunc'
    defs: List[A.Name] = field(default_factory=list)
    uses: List[A.Name] = field(default_factory=list)
    # number of assignments (Assign targets) after definition
    writes: int = 0


class _Scope:
    def __init__(self, parent: Optional["_Scope"], func_boundary: bool = False):
        self.parent = parent
        self.func_boundary = func_boundary
        self.names: Dict[str, int] = {}

    def declare(self, name: str, bid: int):
        self.names[name] = bid

    def lookup(self, name: str) -> Optional[int]:
        s = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None


class Resolver:
    def __init__(self):
        self.bindings: Dict[int, Binding] = {}
        self._next = 1

    def _new_binding(self, name: str, kind: str) -> int:
        bid = self._next
        self._next += 1
        self.bindings[bid] = Binding(id=bid, name=name, kind=kind)
        return bid

    def resolve(self, chunk: A.Chunk):
        top = _Scope(None, func_boundary=True)
        self._block(chunk.body, top)
        return self

    # -- helpers -----------------------------------------------------
    def _declare(self, scope: _Scope, name_node: A.Name, kind: str) -> int:
        bid = self._new_binding(name_node.name, kind)
        scope.declare(name_node.name, bid)
        name_node.binding = bid
        self.bindings[bid].defs.append(name_node)
        return bid

    def _use(self, scope: _Scope, name_node: A.Name):
        bid = scope.lookup(name_node.name)
        name_node.binding = bid
        if bid is not None:
            self.bindings[bid].uses.append(name_node)

    # -- blocks / statements ---------------------------------------
    def _block(self, block: A.Block, parent: _Scope):
        scope = _Scope(parent)
        for st in block.stmts:
            self._stat(st, scope)

    def _stat(self, s: A.Node, scope: _Scope):
        k = s.kind
        if k == "LocalAssign":
            for v in s.values:
                self._expr(v, scope)
            for t in s.targets:
                self._declare(scope, t, "local")
        elif k == "LocalFunction":
            self._declare(scope, s.name, "localfunc")
            self._function(s.func, scope)
        elif k == "Assign":
            for v in s.values:
                self._expr(v, scope)
            for t in s.targets:
                self._expr(t, scope)
                if isinstance(t, A.Name) and t.binding is not None:
                    self.bindings[t.binding].writes += 1
        elif k == "CompoundAssign":
            self._expr(s.value, scope)
            self._expr(s.target, scope)
        elif k == "CallStat":
            self._expr(s.call, scope)
        elif k == "Do":
            self._block(s.body, scope)
        elif k == "While":
            self._expr(s.cond, scope)
            self._block(s.body, scope)
        elif k == "Repeat":
            # 'until' can see body locals -> share one scope
            inner = _Scope(scope)
            for st in s.body.stmts:
                self._stat(st, inner)
            self._expr(s.cond, inner)
        elif k == "If":
            for cond, body in s.clauses:
                self._expr(cond, scope)
                self._block(body, scope)
            if s.orelse is not None:
                self._block(s.orelse, scope)
        elif k == "NumericFor":
            self._expr(s.start, scope)
            self._expr(s.stop, scope)
            if s.step is not None:
                self._expr(s.step, scope)
            inner = _Scope(scope)
            self._declare(inner, s.var, "loop")
            for st in s.body.stmts:
                self._stat(st, inner)
        elif k == "GenericFor":
            for e in s.exprs:
                self._expr(e, scope)
            inner = _Scope(scope)
            for n in s.names:
                self._declare(inner, n, "loop")
            for st in s.body.stmts:
                self._stat(st, inner)
        elif k == "FunctionDecl":
            self._expr(s.target, scope)
            self._function(s.func, scope)
        elif k == "Return":
            for v in s.values:
                self._expr(v, scope)
        elif k in ("Break", "Continue", "Goto", "Label"):
            pass
        else:
            # unknown statement: walk generically
            for child in A.iter_child_nodes(s):
                self._maybe(child, scope)

    def _function(self, fn: A.Function, parent: _Scope):
        scope = _Scope(parent, func_boundary=True)
        for p in fn.params:
            self._declare(scope, p, "param")
        for st in fn.body.stmts:
            self._stat(st, scope)

    # -- expressions -----------------------------------------------
    def _maybe(self, node, scope):
        if isinstance(node, A.Node):
            self._expr(node, scope)

    def _expr(self, e: A.Node, scope: _Scope):
        k = e.kind
        if k == "Name":
            self._use(scope, e)
        elif k == "Function":
            self._function(e, scope)
        elif k == "Index":
            self._expr(e.obj, scope)
            self._expr(e.key, scope)
        elif k == "Call":
            self._expr(e.func, scope)
            for a in e.args:
                self._expr(a, scope)
        elif k == "MethodCall":
            self._expr(e.obj, scope)
            for a in e.args:
                self._expr(a, scope)
        elif k == "BinOp":
            self._expr(e.left, scope)
            self._expr(e.right, scope)
        elif k == "UnOp":
            self._expr(e.operand, scope)
        elif k == "Paren":
            self._expr(e.inner, scope)
        elif k == "Table":
            for kind, key, val in e.fields:
                if isinstance(key, A.Node):
                    self._expr(key, scope)
                self._expr(val, scope)
        elif k in ("Number", "String", "Nil", "TrueExpr", "FalseExpr", "Vararg"):
            pass
        else:
            for child in A.iter_child_nodes(e):
                self._maybe(child, scope)


def resolve(chunk: A.Chunk) -> Resolver:
    return Resolver().resolve(chunk)
