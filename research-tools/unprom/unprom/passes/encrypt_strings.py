"""Reverse ``EncryptStrings``.

Obfuscated shape (after Prometheus' own renamer)::

    local D, S                       -- two locals, declared empty, shuffled order
    do
        local function get_next_pseudo_random_byte() ... end   -- keystream lives here
        S = setmetatable({}, { __index = realStrings, __metatable = nil })
        function D(str, seed) ... end -- fills realStrings[seed], returns seed
    end
    ...  S[D("<cipher>", 123456)] ...  -- every original string literal

The keystream constants ``param_mul_45`` / ``param_add_45`` (from the
``(x * K1 + K2) % 35184372088832`` update), ``param_mul_8`` (from
``state_8 * K % 257``) and ``secret_key_8`` (the ``local prevVal = <n>`` init)
are baked into the prelude, so every ``S[D(c, seed)]`` becomes a plain literal
again.  The ``do`` block and the now-unused ``local D, S`` decl are removed.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..astutil import as_int, binding_of, name_of, unparen
from ..scope import resolve
from ..prng import decrypt

_MOD45 = 35184372088832.0


def _find_seed_modulus(node: A.Node):
    """Locate ``(x * K1 + K2) % 35184372088832`` -> (K1, K2)."""
    for n in A.walk(node):
        if not (isinstance(n, A.BinOp) and n.op == "%"):
            continue
        if not (isinstance(unparen(n.right), A.Number)
                and unparen(n.right).value == _MOD45):
            continue
        add = unparen(n.left)
        if isinstance(add, A.BinOp) and add.op == "+":
            mul = unparen(add.left)
            k2 = as_int(add.right)
            if isinstance(mul, A.BinOp) and mul.op == "*":
                k1 = as_int(mul.right)
                if k1 is None:
                    k1 = as_int(mul.left)
                if k1 is not None and k2 is not None:
                    return k1, k2
    return None


def _find_mul_8(node: A.Node):
    """``state_8 = state_8 * K % 257`` -> K."""
    for n in A.walk(node):
        if isinstance(n, A.BinOp) and n.op == "%":
            if isinstance(unparen(n.right), A.Number) and unparen(n.right).value == 257.0:
                mul = unparen(n.left)
                if isinstance(mul, A.BinOp) and mul.op == "*":
                    return as_int(mul.right) if as_int(mul.right) is not None \
                        else as_int(mul.left)
    return None


def _find_secret_key_8(node: A.Node):
    for n in A.walk(node):
        if isinstance(n, A.LocalAssign) and len(n.values) == 1:
            v = as_int(n.values[0])
            if v is not None and 0 <= v <= 255:
                return v
    return None


def _strings_binding(do: A.Do):
    for n in A.walk(do):
        if isinstance(n, A.Assign) and len(n.values) == 1:
            v = unparen(n.values[0])
            if isinstance(v, A.Call) and name_of(v.func) == "setmetatable":
                return binding_of(n.targets[0])
    return None


def _decrypt_binding(do: A.Do, outer: set, s_bind):
    for n in A.walk(do):
        fn = target = None
        if isinstance(n, A.FunctionDecl) and not n.is_method:
            fn, target = n.func, n.target
        elif isinstance(n, A.Assign) and len(n.values) == 1 \
                and isinstance(unparen(n.values[0]), A.Function):
            fn, target = unparen(n.values[0]), n.targets[0]
        if fn is None or len(fn.params) < 2:
            continue
        b = binding_of(target)
        if b is not None and b in outer and b != s_bind:
            return b
    return None


def _find_prelude(chunk: A.Chunk):
    stmts = chunk.body.stmts
    for i in range(len(stmts) - 1):
        decl, do = stmts[i], stmts[i + 1]
        if not (isinstance(decl, A.LocalAssign) and len(decl.targets) == 2
                and not decl.values):
            continue
        if not isinstance(do, A.Do):
            continue
        seedmod = _find_seed_modulus(do)
        if seedmod is None:
            continue
        outer = {t.binding for t in decl.targets}
        s_bind = _strings_binding(do)
        d_bind = _decrypt_binding(do, outer, s_bind)
        mul8 = _find_mul_8(do)
        key8 = _find_secret_key_8(do)
        if None in (s_bind, d_bind, mul8, key8):
            continue
        consts = (seedmod[0], seedmod[1], mul8, key8)
        return decl, do, d_bind, s_bind, consts
    return None


class _Rewrite(A.NodeTransformer):
    def __init__(self, s_bind, d_bind, consts, ctx):
        self.s_bind, self.d_bind, self.consts, self.ctx = s_bind, d_bind, consts, ctx
        self.failures = 0

    def visit_Index(self, node: A.Index):
        node = self.generic_visit(node)
        if binding_of(node.obj) != self.s_bind:
            return node
        key = unparen(node.key)
        if not (isinstance(key, A.Call) and binding_of(key.func) == self.d_bind
                and len(key.args) == 2):
            return node
        cipher = unparen(key.args[0])
        seed = as_int(key.args[1])
        if not isinstance(cipher, A.String) or seed is None:
            return node
        try:
            plain = decrypt(cipher.value, seed, *self.consts)
        except Exception as exc:  # noqa: BLE001
            self.ctx.note(f"[encrypt_strings] decrypt failed: {exc!r}")
            self.failures += 1
            return node
        self.ctx.bump("strings_decrypted")
        return A.String(value=plain, line=node.line)


def run(chunk: A.Chunk, ctx) -> A.Chunk:
    resolve(chunk)
    found = _find_prelude(chunk)
    if not found:
        return chunk
    decl, do, d_bind, s_bind, consts = found
    ctx.note(f"[encrypt_strings] keystream consts = {consts}")
    rw = _Rewrite(s_bind, d_bind, consts, ctx)
    chunk = rw.visit(chunk)

    inlined = ctx.stats.get("strings_decrypted", 0)
    if inlined and not rw.failures:
        chunk.body.stmts = [s for s in chunk.body.stmts
                            if s is not do and s is not decl]
        ctx.note(f"[encrypt_strings] inlined {inlined} string(s), removed prelude")
    else:
        ctx.note(f"[encrypt_strings] inlined {inlined} string(s); "
                 f"{rw.failures} failure(s); prelude kept")
    return chunk
