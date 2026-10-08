"""A tokenizer for Lua 5.1 / 5.2 source, with a few Luau tolerances.

Prometheus emits Lua 5.1 by default (LuaVersion = "Lua51") and, with
PrettyPrint disabled, produces a single very long line.  The lexer below is
deliberately permissive: it also accepts ``goto`` / ``::label::`` (5.2), the
Luau compound-assignment operators (``+=`` ...), ``//`` integer division and
``continue`` as a soft keyword so that partially-Luau output still tokenizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
}

# Multi-character operators, longest first so the scanner is greedy.
_SYMBOLS = [
    "...", "..", "::", "<<", ">>", "//",
    "==", "~=", "<=", ">=",
    "+=", "-=", "*=", "/=", "%=", "^=", "..=",
    "+", "-", "*", "/", "%", "^", "#", "&", "~", "|", "<", ">", "=",
    "(", ")", "{", "}", "[", "]", ";", ":", ",", ".",
]


class LuaSyntaxError(SyntaxError):
    pass


@dataclass
class Token:
    type: str          # NAME, NUMBER, STRING, KEYWORD, OP, EOF
    value: str         # raw-ish value; for STRING this is the decoded text
    line: int
    col: int
    # For strings we remember how they were written so the unparser can round
    # trip long-bracket literals when it wants to.
    raw: Optional[str] = None

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        v = self.value if len(self.value) < 24 else self.value[:21] + "..."
        return f"Token({self.type}, {v!r}, {self.line}:{self.col})"


class Lexer:
    def __init__(self, src: str):
        self.src = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.col = 1

    # -- low level helpers -------------------------------------------------
    def _peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.src[j] if j < self.n else ""

    def _advance(self) -> str:
        c = self.src[self.i]
        self.i += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def _match(self, s: str) -> bool:
        if self.src.startswith(s, self.i):
            for _ in s:
                self._advance()
            return True
        return False

    def error(self, msg: str):
        raise LuaSyntaxError(f"{msg} at line {self.line} col {self.col}")

    # -- long brackets [[ ]] / [==[ ]==] --------------------------------
    def _read_long_bracket(self) -> Optional[str]:
        """If positioned on a long-bracket opener, consume and return contents.

        Returns None when there is no opener here (so a bare ``[`` stays an op).
        """
        start = self.i
        if self._peek() != "[":
            return None
        j = self.i + 1
        level = 0
        while j < self.n and self.src[j] == "=":
            level += 1
            j += 1
        if j >= self.n or self.src[j] != "[":
            return None
        # commit
        for _ in range(2 + level):
            self._advance()
        # A newline immediately after the opener is skipped by Lua.
        if self._peek() == "\r":
            self._advance()
        if self._peek() == "\n":
            self._advance()
        close = "]" + "=" * level + "]"
        idx = self.src.find(close, self.i)
        if idx == -1:
            self.i = start
            self.error("unterminated long bracket")
        text = self.src[self.i:idx]
        while self.i < idx + len(close):
            self._advance()
        return text

    # -- string escapes -------------------------------------------------
    def _read_quoted(self, q: str) -> str:
        self._advance()  # opening quote
        out: List[str] = []
        while True:
            if self.i >= self.n:
                self.error("unterminated string")
            c = self._advance()
            if c == q:
                break
            if c == "\n":
                self.error("unterminated string")
            if c != "\\":
                out.append(c)
                continue
            e = self._advance()
            simple = {
                "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
                "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'",
                "\n": "\n", "\r": "\r",
            }
            if e in simple:
                out.append(simple[e])
            elif e == "x":
                h = ""
                for _ in range(2):
                    if self._peek() and self._peek() in "0123456789abcdefABCDEF":
                        h += self._advance()
                if not h:
                    self.error("malformed \\x escape")
                out.append(chr(int(h, 16)))
            elif e == "z":
                while self._peek() and self._peek() in " \t\r\n\f\v":
                    self._advance()
            elif e.isdigit():
                d = e
                for _ in range(2):
                    if self._peek().isdigit():
                        d += self._advance()
                v = int(d)
                if v > 255:
                    self.error("decimal escape too large")
                out.append(chr(v))
            elif e == "u":
                if self._peek() != "{":
                    self.error("malformed \\u escape")
                self._advance()
                h = ""
                while self._peek() and self._peek() != "}":
                    h += self._advance()
                self._advance()
                out.append(chr(int(h, 16)))
            else:
                # Unknown escape: keep the char (permissive).
                out.append(e)
        return "".join(out)

    # -- numbers ------------------------------------------------------------
    def _read_number(self) -> str:
        start = self.i
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance()
            self._advance()
            while self._peek() and (self._peek() in "0123456789abcdefABCDEF.pP" or
                                    (self._peek() in "+-" and self.src[self.i - 1] in "pP")):
                self._advance()
        else:
            while self._peek() and (self._peek().isdigit() or self._peek() in ".eE" or
                                    (self._peek() in "+-" and self.src[self.i - 1] in "eE")):
                self._advance()
        return self.src[start:self.i]

    # -- main loop --------------------------------------------------------
    def tokens(self) -> List[Token]:
        toks: List[Token] = []
        # Skip an initial shebang line, which Lua allows.
        if self.src.startswith("#"):
            while self.i < self.n and self._peek() != "\n":
                self._advance()
        while True:
            self._skip_trivia()
            if self.i >= self.n:
                toks.append(Token("EOF", "", self.line, self.col))
                return toks
            line, col = self.line, self.col
            c = self._peek()

            if c in "'\"":
                raw_start = self.i
                text = self._read_quoted(c)
                toks.append(Token("STRING", text, line, col,
                                  raw=self.src[raw_start:self.i]))
                continue

            if c == "[" and self._peek(1) in ("[", "="):
                raw_start = self.i
                lb = self._read_long_bracket()
                if lb is not None:
                    toks.append(Token("STRING", lb, line, col,
                                      raw=self.src[raw_start:self.i]))
                    continue

            if c.isdigit() or (c == "." and self._peek(1).isdigit()):
                toks.append(Token("NUMBER", self._read_number(), line, col))
                continue

            if c.isalpha() or c == "_":
                start = self.i
                while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
                    self._advance()
                word = self.src[start:self.i]
                ttype = "KEYWORD" if word in KEYWORDS else "NAME"
                toks.append(Token(ttype, word, line, col))
                continue

            for sym in _SYMBOLS:
                if self.src.startswith(sym, self.i):
                    for _ in sym:
                        self._advance()
                    toks.append(Token("OP", sym, line, col))
                    break
            else:
                self.error(f"unexpected character {c!r}")

    def _skip_trivia(self):
        while self.i < self.n:
            c = self._peek()
            if c in " \t\r\n\f\v":
                self._advance()
                continue
            if c == "-" and self._peek(1) == "-":
                self._advance()
                self._advance()
                lb = self._read_long_bracket()
                if lb is not None:
                    continue
                while self.i < self.n and self._peek() != "\n":
                    self._advance()
                continue
            return


def tokenize(src: str) -> List[Token]:
    return Lexer(src).tokens()
