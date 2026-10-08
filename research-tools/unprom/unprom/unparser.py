"""AST -> formatted Lua source.

Precedence-aware so the emitted text re-parses to an equivalent tree; keeps
things readable with 2-space indentation.
"""

from __future__ import annotations

import keyword as _pykw
from typing import List

from . import ast_nodes as A
from .lexer import KEYWORDS


_BIN_PREC = {
    "or": 1, "and": 2,
    "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3,
    "|": 4, "~": 5, "&": 6, "<<": 7, ">>": 7,
    "..": 9,
    "+": 10, "-": 10,
    "*": 11, "/": 11, "//": 11, "%": 11,
    "^": 14,
}
_UNARY_PREC = 12
_RIGHT_ASSOC = {"..", "^"}


def _is_ident(s: str) -> bool:
    if not s or (not s[0].isalpha() and s[0] != "_"):
        return False
    return all(c.isalnum() or c == "_" for c in s) and s not in KEYWORDS


def _quote(s: str) -> str:
    # Prefer double quotes; escape control chars and the quote/backslash.
    has_d = '"' in s
    has_s = "'" in s
    q = '"'
    if has_d and not has_s:
        q = "'"
    out = [q]
    for ch in s:
        o = ord(ch)
        if ch == q:
            out.append("\\" + ch)
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o >= 127:
            # Every non-printable / non-ASCII byte as a 3-digit numeric escape
            # (Lua reads at most 3 digits), so the string round-trips exactly.
            out.append("\\%03d" % o)
        else:
            out.append(ch)
    out.append(q)
    return "".join(out)


def _fmt_number(n: A.Number) -> str:
    v = n.value
    if v != v:  # nan
        return "(0/0)"
    if v == float("inf"):
        return "(1/0)"
    if v == float("-inf"):
        return "(-1/0)"
    if v == int(v) and abs(v) < 1e15:
        iv = int(v)
        # Reuse hex spelling when the source used one and it is still tidy.
        if n.raw and n.raw.strip().lower().startswith("0x") and "." not in n.raw:
            return hex(iv) if iv >= 0 else "-" + hex(-iv)
        return str(iv)
    r = repr(v)
    return r


class Unparser:
    def __init__(self, indent: str = "  "):
        self.indent = indent
        self.out: List[str] = []
        self.level = 0

    # -- helpers ------------------------------------------------------
    def _line(self, text: str):
        self.out.append(self.indent * self.level + text)

    def emit(self, chunk: A.Chunk) -> str:
        self._block(chunk.body)
        return "\n".join(self.out) + "\n"

    def _block(self, block: A.Block):
        for st in block.stmts:
            self._stat(st)

    # -- statements -------------------------------------------------
    def _stat(self, s: A.Node):
        m = getattr(self, "_st_" + s.kind, None)
        if m is None:
            raise NotImplementedError(f"unparse statement {s.kind}")
        before = len(self.out)
        m(s)
        # A statement that begins with '(' can be glued onto the previous
        # statement by Lua's grammar (`a = b` newline `(f)()` parses as
        # `a = b(f)()`).  Guard it with a leading empty statement -- but only
        # when there actually is a preceding line.
        if before > 0 and len(self.out) > before:
            if self.out[before].lstrip().startswith("("):
                self.out[before] = self.out[before].replace("(", ";(", 1)

    def _st_LocalAssign(self, s: A.LocalAssign):
        names = []
        for i, t in enumerate(s.targets):
            nm = self._sanitize(t.name)
            if i < len(s.attribs) and s.attribs[i]:
                nm += " <%s>" % s.attribs[i]
            names.append(nm)
        text = "local " + ", ".join(names)
        if s.values:
            text += " = " + ", ".join(self._expr(v) for v in s.values)
        self._line(text)

    def _st_Assign(self, s: A.Assign):
        lhs = ", ".join(self._expr(t) for t in s.targets)
        rhs = ", ".join(self._expr(v) for v in s.values)
        self._line(f"{lhs} = {rhs}")

    def _st_CompoundAssign(self, s: A.CompoundAssign):
        self._line(f"{self._expr(s.target)} {s.op}= {self._expr(s.value)}")

    def _st_CallStat(self, s: A.CallStat):
        self._line(self._expr(s.call))

    def _st_Do(self, s: A.Do):
        self._line("do")
        self.level += 1
        self._block(s.body)
        self.level -= 1
        self._line("end")

    def _st_While(self, s: A.While):
        self._line(f"while {self._expr(s.cond)} do")
        self.level += 1
        self._block(s.body)
        self.level -= 1
        self._line("end")

    def _st_Repeat(self, s: A.Repeat):
        self._line("repeat")
        self.level += 1
        self._block(s.body)
        self.level -= 1
        self._line(f"until {self._expr(s.cond)}")

    def _st_If(self, s: A.If):
        for i, (cond, body) in enumerate(s.clauses):
            kw = "if" if i == 0 else "elseif"
            self._line(f"{kw} {self._expr(cond)} then")
            self.level += 1
            self._block(body)
            self.level -= 1
        if s.orelse is not None:
            self._line("else")
            self.level += 1
            self._block(s.orelse)
            self.level -= 1
        self._line("end")

    def _st_NumericFor(self, s: A.NumericFor):
        parts = [self._expr(s.start), self._expr(s.stop)]
        if s.step is not None:
            parts.append(self._expr(s.step))
        self._line(f"for {self._sanitize(s.var.name)} = {', '.join(parts)} do")
        self.level += 1
        self._block(s.body)
        self.level -= 1
        self._line("end")

    def _st_GenericFor(self, s: A.GenericFor):
        names = ", ".join(self._sanitize(n.name) for n in s.names)
        exprs = ", ".join(self._expr(e) for e in s.exprs)
        self._line(f"for {names} in {exprs} do")
        self.level += 1
        self._block(s.body)
        self.level -= 1
        self._line("end")

    def _st_FunctionDecl(self, s: A.FunctionDecl):
        name = self._func_target(s.target, s.is_method)
        params = self._params(s.func, skip_self=s.is_method)
        self._line(f"function {name}({params})")
        self.level += 1
        self._block(s.func.body)
        self.level -= 1
        self._line("end")

    def _st_LocalFunction(self, s: A.LocalFunction):
        params = self._params(s.func)
        self._line(f"local function {self._sanitize(s.name.name)}({params})")
        self.level += 1
        self._block(s.func.body)
        self.level -= 1
        self._line("end")

    def _st_Return(self, s: A.Return):
        if s.values:
            self._line("return " + ", ".join(self._expr(v) for v in s.values))
        else:
            self._line("return")

    def _st_Break(self, s: A.Break):
        self._line("break")

    def _st_Continue(self, s: A.Continue):
        self._line("continue")

    def _st_Goto(self, s: A.Goto):
        self._line(f"goto {s.label}")

    def _st_Label(self, s: A.Label):
        self._line(f"::{s.name}::")

    # -- function bits ------------------------------------------------
    def _params(self, fn: A.Function, skip_self: bool = False) -> str:
        names = [self._sanitize(p.name) for p in fn.params]
        if skip_self and names and names[0] == "self":
            names = names[1:]
        if fn.is_vararg:
            names.append("...")
        return ", ".join(names)

    def _func_target(self, target: A.Node, is_method: bool) -> str:
        # target is Name / Index chain of dotted string keys
        parts = []
        node = target
        chain = []
        while isinstance(node, A.Index):
            chain.append(node.key.value)
            node = node.obj
        chain.append(node.name)
        chain.reverse()
        if is_method:
            head = ".".join(chain[:-1])
            return f"{head}:{chain[-1]}"
        return ".".join(chain)

    # -- expressions ------------------------------------------------
    def _sanitize(self, name: str) -> str:
        return name

    def _expr(self, e: A.Node, parent_prec: int = 0, right: bool = False) -> str:
        m = getattr(self, "_ex_" + e.kind, None)
        if m is None:
            raise NotImplementedError(f"unparse expr {e.kind}")
        return m(e, parent_prec, right)

    def _ex_Nil(self, e, *_):
        return "nil"

    def _ex_TrueExpr(self, e, *_):
        return "true"

    def _ex_FalseExpr(self, e, *_):
        return "false"

    def _ex_Vararg(self, e, *_):
        return "..."

    def _ex_Number(self, e, *_):
        return _fmt_number(e)

    def _ex_String(self, e, *_):
        return _quote(e.value)

    def _ex_Name(self, e, *_):
        return self._sanitize(e.name)

    def _ex_Index(self, e: A.Index, *_):
        base = self._prefix(e.obj)
        if e.dot and isinstance(e.key, A.String) and _is_ident(e.key.value):
            return f"{base}.{e.key.value}"
        return f"{base}[{self._expr(e.key)}]"

    def _ex_Call(self, e: A.Call, *_):
        return f"{self._prefix(e.func)}({', '.join(self._expr(a) for a in e.args)})"

    def _ex_MethodCall(self, e: A.MethodCall, *_):
        return (f"{self._prefix(e.obj)}:{e.method}"
                f"({', '.join(self._expr(a) for a in e.args)})")

    def _ex_Paren(self, e: A.Paren, *_):
        return f"({self._expr(e.inner)})"

    def _ex_Function(self, e: A.Function, *_):
        params = self._params(e)
        sub = Unparser(self.indent)
        sub.level = self.level + 1
        sub._block(e.body)
        body = "\n".join(sub.out)
        pad = self.indent * self.level
        if body:
            return f"function({params})\n{body}\n{pad}end"
        return f"function({params}) end"

    def _ex_Table(self, e: A.Table, *_):
        if not e.fields:
            return "{}"
        parts = []
        for kind, key, val in e.fields:
            if kind == "pos":
                parts.append(self._expr(val))
            elif kind == "name":
                parts.append(f"{key} = {self._expr(val)}")
            else:
                if isinstance(key, A.String) and _is_ident(key.value):
                    parts.append(f"{key.value} = {self._expr(val)}")
                else:
                    parts.append(f"[{self._expr(key)}] = {self._expr(val)}")
        inline = "{" + ", ".join(parts) + "}"
        if len(inline) <= 78 and "\n" not in inline:
            return inline
        pad = self.indent * (self.level + 1)
        end = self.indent * self.level
        return "{\n" + ",\n".join(pad + p for p in parts) + "\n" + end + "}"

    def _ex_UnOp(self, e: A.UnOp, parent_prec=0, right=False):
        inner = self._expr(e.operand, _UNARY_PREC, right=True)
        if e.op == "not":
            op = "not "
        elif e.op == "-" and inner[:1] == "-":
            op = "- "          # avoid forming a `--` comment
        else:
            op = e.op
        text = f"{op}{inner}"
        if _UNARY_PREC < parent_prec:
            return f"({text})"
        return text

    def _ex_BinOp(self, e: A.BinOp, parent_prec=0, right=False):
        prec = _BIN_PREC[e.op]
        la = prec + (1 if e.op in _RIGHT_ASSOC else 0)
        ra = prec + (0 if e.op in _RIGHT_ASSOC else 1)
        lhs = self._expr(e.left, la)
        rhs = self._expr(e.right, ra)
        text = f"{lhs} {e.op} {rhs}"
        need = prec < parent_prec or (prec == parent_prec and right)
        return f"({text})" if need else text

    def _prefix(self, e: A.Node) -> str:
        """Render something in prefix-expression position, adding parens when
        the grammar requires them (literals, operators, function bodies)."""
        if isinstance(e, (A.Name, A.Index, A.Call, A.MethodCall, A.Paren)):
            return self._expr(e)
        return f"({self._expr(e)})"


def unparse(chunk: A.Chunk, indent: str = "  ") -> str:
    return Unparser(indent).emit(chunk)
