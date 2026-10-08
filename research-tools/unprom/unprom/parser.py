"""Recursive-descent parser: token stream -> AST (see :mod:`unprom.ast_nodes`).

Targets Lua 5.1 grammar (what Prometheus emits by default) plus:
  * ``goto`` / ``::label::``           (Lua 5.2)
  * bitwise ``& | ~ << >>`` and ``//``  (Lua 5.3 - accepted, low precedence)
  * ``continue`` as a statement          (Luau)
  * compound assignment ``+= -= ...``    (Luau)
"""

from __future__ import annotations

from typing import List, Optional

from .lexer import Token, tokenize, LuaSyntaxError
from . import ast_nodes as A


# name -> (left binding power, right binding power); right < left => right assoc
_BINPRI = {
    "or": (1, 2), "and": (3, 4),
    "<": (5, 6), ">": (5, 6), "<=": (5, 6), ">=": (5, 6), "~=": (5, 6), "==": (5, 6),
    "|": (7, 8), "~": (9, 10), "&": (11, 12),
    "<<": (13, 14), ">>": (13, 14),
    "..": (18, 17),                       # right assoc
    "+": (19, 20), "-": (19, 20),
    "*": (21, 22), "/": (21, 22), "//": (21, 22), "%": (21, 22),
    "^": (28, 27),                        # right assoc, binds tighter than unary
}
_UNARY_PRI = 25
_UNARY_OPS = {"not", "-", "#", "~"}


class Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.p = 0

    # -- token helpers --------------------------------------------------
    @property
    def cur(self) -> Token:
        return self.toks[self.p]

    def _next(self) -> Token:
        t = self.toks[self.p]
        if t.type != "EOF":
            self.p += 1
        return t

    def _check(self, type_: str, value: Optional[str] = None) -> bool:
        t = self.cur
        if t.type != type_:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def _accept(self, type_: str, value: Optional[str] = None) -> Optional[Token]:
        if self._check(type_, value):
            return self._next()
        return None

    def _expect(self, type_: str, value: Optional[str] = None) -> Token:
        if not self._check(type_, value):
            want = value or type_
            got = self.cur
            raise LuaSyntaxError(
                f"expected {want!r} but got {got.type}:{got.value!r} "
                f"at line {got.line} col {got.col}")
        return self._next()

    def _is_kw(self, *words: str) -> bool:
        return self.cur.type == "KEYWORD" and self.cur.value in words

    def _is_op(self, *ops: str) -> bool:
        return self.cur.type == "OP" and self.cur.value in ops

    # -- entry --------------------------------------------------------
    def parse_chunk(self) -> A.Chunk:
        blk = self._block()
        self._expect("EOF")
        return A.Chunk(body=blk, line=1)

    # -- blocks -----------------------------------------------------------
    _BLOCK_END = {"end", "else", "elseif", "until"}

    def _at_block_end(self) -> bool:
        if self.cur.type == "EOF":
            return True
        if self.cur.type == "KEYWORD" and self.cur.value in self._BLOCK_END:
            return True
        return False

    def _block(self) -> A.Block:
        line = self.cur.line
        stmts: List[A.Node] = []
        while not self._at_block_end():
            if self._is_kw("return"):
                stmts.append(self._return_stat())
                break
            s = self._statement()
            if s is not None:
                stmts.append(s)
        return A.Block(stmts=stmts, line=line)

    def _return_stat(self) -> A.Return:
        line = self._next().line  # 'return'
        values: List[A.Node] = []
        if not self._at_block_end() and not self._is_op(";"):
            values = self._exprlist()
        self._accept("OP", ";")
        return A.Return(values=values, line=line)

    # -- statements -----------------------------------------------------
    def _statement(self) -> Optional[A.Node]:
        t = self.cur
        line = t.line

        if self._accept("OP", ";"):
            return None

        if self._accept("OP", "::"):
            name = self._expect("NAME").value
            self._expect("OP", "::")
            return A.Label(name=name, line=line)

        if t.type == "KEYWORD":
            kw = t.value
            if kw == "break":
                self._next()
                return A.Break(line=line)
            if kw == "goto":
                self._next()
                return A.Goto(label=self._expect("NAME").value, line=line)
            if kw == "do":
                self._next()
                body = self._block()
                self._expect("KEYWORD", "end")
                return A.Do(body=body, line=line)
            if kw == "while":
                self._next()
                cond = self._expr()
                self._expect("KEYWORD", "do")
                body = self._block()
                self._expect("KEYWORD", "end")
                return A.While(cond=cond, body=body, line=line)
            if kw == "repeat":
                self._next()
                body = self._block()
                self._expect("KEYWORD", "until")
                cond = self._expr()
                return A.Repeat(body=body, cond=cond, line=line)
            if kw == "if":
                return self._if_stat()
            if kw == "for":
                return self._for_stat()
            if kw == "function":
                return self._function_stat()
            if kw == "local":
                return self._local_stat()

        # Luau soft keyword
        if t.type == "NAME" and t.value == "continue" and self._looks_like_continue():
            self._next()
            return A.Continue(line=line)

        return self._expr_stat()

    def _looks_like_continue(self) -> bool:
        nxt = self.toks[self.p + 1]
        if nxt.type == "EOF":
            return True
        if nxt.type == "KEYWORD" and nxt.value in self._BLOCK_END:
            return True
        if nxt.type == "OP" and nxt.value == ";":
            return True
        # 'continue' followed by another statement-starting name/keyword on a
        # fresh line is still ambiguous; treat as continue only in the safe set.
        return nxt.type == "KEYWORD" and nxt.value in {
            "if", "for", "while", "do", "local", "return", "repeat", "break"}

    def _if_stat(self) -> A.If:
        line = self._next().line  # 'if'
        clauses = []
        cond = self._expr()
        self._expect("KEYWORD", "then")
        clauses.append((cond, self._block()))
        while self._is_kw("elseif"):
            self._next()
            c = self._expr()
            self._expect("KEYWORD", "then")
            clauses.append((c, self._block()))
        orelse = None
        if self._accept("KEYWORD", "else"):
            orelse = self._block()
        self._expect("KEYWORD", "end")
        return A.If(clauses=clauses, orelse=orelse, line=line)

    def _for_stat(self) -> A.Node:
        line = self._next().line  # 'for'
        first = self._expect("NAME").value
        if self._accept("OP", "="):
            start = self._expr()
            self._expect("OP", ",")
            stop = self._expr()
            step = None
            if self._accept("OP", ","):
                step = self._expr()
            self._expect("KEYWORD", "do")
            body = self._block()
            self._expect("KEYWORD", "end")
            return A.NumericFor(var=A.Name(name=first, line=line), start=start,
                                stop=stop, step=step, body=body, line=line)
        names = [A.Name(name=first, line=line)]
        while self._accept("OP", ","):
            names.append(A.Name(name=self._expect("NAME").value, line=line))
        self._expect("KEYWORD", "in")
        exprs = self._exprlist()
        self._expect("KEYWORD", "do")
        body = self._block()
        self._expect("KEYWORD", "end")
        return A.GenericFor(names=names, exprs=exprs, body=body, line=line)

    def _funcname(self):
        line = self.cur.line
        node: A.Node = A.Name(name=self._expect("NAME").value, line=line)
        while self._accept("OP", "."):
            key = self._expect("NAME").value
            node = A.Index(obj=node, key=A.String(value=key), dot=True, line=line)
        is_method = False
        if self._accept("OP", ":"):
            key = self._expect("NAME").value
            node = A.Index(obj=node, key=A.String(value=key), dot=True, line=line)
            is_method = True
        return node, is_method

    def _function_stat(self) -> A.FunctionDecl:
        line = self._next().line  # 'function'
        target, is_method = self._funcname()
        func = self._funcbody(line, implicit_self=is_method)
        return A.FunctionDecl(target=target, is_method=is_method, func=func, line=line)

    def _funcbody(self, line: int, implicit_self: bool = False) -> A.Function:
        self._expect("OP", "(")
        params: List[A.Name] = []
        if implicit_self:
            params.append(A.Name(name="self", line=line))
        is_vararg = False
        if not self._is_op(")"):
            while True:
                if self._accept("OP", "..."):
                    is_vararg = True
                    break
                params.append(A.Name(name=self._expect("NAME").value, line=line))
                if not self._accept("OP", ","):
                    break
        self._expect("OP", ")")
        # Luau return-type annotation:  ): Type
        if self._accept("OP", ":"):
            self._skip_type()
        body = self._block()
        self._expect("KEYWORD", "end")
        return A.Function(params=params, is_vararg=is_vararg, body=body, line=line)

    def _local_stat(self) -> A.Node:
        line = self._next().line  # 'local'
        if self._accept("KEYWORD", "function"):
            name = A.Name(name=self._expect("NAME").value, line=line)
            func = self._funcbody(line)
            return A.LocalFunction(name=name, func=func, line=line)
        targets: List[A.Name] = []
        attribs: List[Optional[str]] = []
        while True:
            targets.append(A.Name(name=self._expect("NAME").value, line=line))
            attr = None
            if self._accept("OP", "<"):
                attr = self._expect("NAME").value
                self._expect("OP", ">")
            elif self._accept("OP", ":"):
                self._skip_type()
            attribs.append(attr)
            if not self._accept("OP", ","):
                break
        values: List[A.Node] = []
        if self._accept("OP", "="):
            values = self._exprlist()
        return A.LocalAssign(targets=targets, values=values, attribs=attribs, line=line)

    def _skip_type(self):
        """Best-effort skip of a Luau type expression after ':'."""
        depth = 0
        while True:
            t = self.cur
            if t.type == "EOF":
                return
            if t.type == "OP" and t.value in "([{":
                depth += 1
                self._next()
                continue
            if t.type == "OP" and t.value in ")]}":
                if depth == 0:
                    return
                depth -= 1
                self._next()
                continue
            if depth == 0:
                if t.type == "OP" and t.value in {",", "="}:
                    return
                if t.type == "KEYWORD" and t.value in {
                        "do", "then", "end", "return", "local", "if", "while",
                        "for", "function", "repeat"}:
                    return
                if t.type == "OP" and t.value == ";":
                    return
            self._next()

    def _expr_stat(self) -> A.Node:
        line = self.cur.line
        first = self._suffixed_expr()
        if self._is_op("=", ",", "+=", "-=", "*=", "/=", "%=", "^=", "..="):
            if self.cur.value in ("+=", "-=", "*=", "/=", "%=", "^=", "..="):
                op = self._next().value[:-1]
                val = self._expr()
                return A.CompoundAssign(op=op, target=first, value=val, line=line)
            targets = [first]
            while self._accept("OP", ","):
                targets.append(self._suffixed_expr())
            self._expect("OP", "=")
            values = self._exprlist()
            return A.Assign(targets=targets, values=values, line=line)
        if not isinstance(first, (A.Call, A.MethodCall)):
            raise LuaSyntaxError(
                f"syntax error near line {line}: expression statement is not a call")
        return A.CallStat(call=first, line=line)

    # -- expressions --------------------------------------------------
    def _exprlist(self) -> List[A.Node]:
        exprs = [self._expr()]
        while self._accept("OP", ","):
            exprs.append(self._expr())
        return exprs

    def _expr(self, min_bp: int = 0) -> A.Node:
        line = self.cur.line
        # prefix / unary
        if self._is_op("-", "#", "~") or self._is_kw("not"):
            op = self._next().value
            operand = self._expr(_UNARY_PRI)
            left: A.Node = A.UnOp(op=op, operand=operand, line=line)
        else:
            left = self._simple_expr()

        while True:
            t = self.cur
            opname = None
            if t.type == "OP" and t.value in _BINPRI:
                opname = t.value
            elif t.type == "KEYWORD" and t.value in ("and", "or"):
                opname = t.value
            if opname is None:
                break
            lbp, rbp = _BINPRI[opname]
            if lbp < min_bp:
                break
            self._next()
            right = self._expr(rbp)
            left = A.BinOp(op=opname, left=left, right=right, line=line)
        return left

    def _simple_expr(self) -> A.Node:
        t = self.cur
        line = t.line
        if t.type == "NUMBER":
            self._next()
            return A.Number(value=_num(t.value), raw=t.value, line=line)
        if t.type == "STRING":
            self._next()
            return A.String(value=t.value, raw=t.raw, line=line)
        if t.type == "KEYWORD":
            if t.value == "nil":
                self._next(); return A.Nil(line=line)
            if t.value == "true":
                self._next(); return A.TrueExpr(line=line)
            if t.value == "false":
                self._next(); return A.FalseExpr(line=line)
            if t.value == "function":
                self._next()
                return self._funcbody(line)
        if self._is_op("..."):
            self._next()
            return A.Vararg(line=line)
        if self._is_op("{"):
            return self._table()
        return self._suffixed_expr()

    def _primary_expr(self) -> A.Node:
        t = self.cur
        line = t.line
        if self._accept("OP", "("):
            inner = self._expr()
            self._expect("OP", ")")
            return A.Paren(inner=inner, line=line)
        if t.type == "NAME":
            self._next()
            return A.Name(name=t.value, line=line)
        raise LuaSyntaxError(
            f"unexpected symbol {t.type}:{t.value!r} at line {t.line} col {t.col}")

    def _suffixed_expr(self) -> A.Node:
        node = self._primary_expr()
        while True:
            t = self.cur
            line = t.line
            if self._accept("OP", "."):
                key = self._expect("NAME").value
                node = A.Index(obj=node, key=A.String(value=key), dot=True, line=line)
            elif self._accept("OP", "["):
                key = self._expr()
                self._expect("OP", "]")
                node = A.Index(obj=node, key=key, dot=False, line=line)
            elif self._accept("OP", ":"):
                method = self._expect("NAME").value
                args = self._call_args()
                node = A.MethodCall(obj=node, method=method, args=args, line=line)
            elif t.type == "OP" and t.value in ("(", "{"):
                node = A.Call(func=node, args=self._call_args(), line=line)
            elif t.type == "STRING":
                self._next()
                node = A.Call(func=node,
                              args=[A.String(value=t.value, raw=t.raw, line=line)],
                              line=line)
            else:
                break
        return node

    def _call_args(self) -> List[A.Node]:
        t = self.cur
        if t.type == "STRING":
            self._next()
            return [A.String(value=t.value, raw=t.raw, line=t.line)]
        if self._is_op("{"):
            return [self._table()]
        self._expect("OP", "(")
        args: List[A.Node] = []
        if not self._is_op(")"):
            args = self._exprlist()
        self._expect("OP", ")")
        return args

    def _table(self) -> A.Table:
        line = self._expect("OP", "{").line
        flds = []
        while not self._is_op("}"):
            if self._is_op("["):
                self._next()
                key = self._expr()
                self._expect("OP", "]")
                self._expect("OP", "=")
                val = self._expr()
                flds.append(("expr", key, val))
            elif self.cur.type == "NAME" and self.toks[self.p + 1].type == "OP" \
                    and self.toks[self.p + 1].value == "=":
                key = self._next().value
                self._next()  # '='
                val = self._expr()
                flds.append(("name", key, val))
            else:
                flds.append(("pos", None, self._expr()))
            if not (self._accept("OP", ",") or self._accept("OP", ";")):
                break
        self._expect("OP", "}")
        return A.Table(fields=flds, line=line)


def _num(text: str) -> float:
    s = text.strip()
    try:
        if s[:2].lower() == "0x":
            if any(c in s.lower() for c in ".p"):
                return float.fromhex(s)
            return float(int(s, 16))
        return float(s)
    except ValueError:
        try:
            return float.fromhex(s)
        except ValueError:
            return float("nan")


def parse(src: str) -> A.Chunk:
    return Parser(tokenize(src)).parse_chunk()
