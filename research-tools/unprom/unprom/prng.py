"""Reimplementation of the Prometheus ``EncryptStrings`` keystream.

Ported straight from ``src/prometheus/steps/EncryptStrings.lua``.  The obfuscated
script embeds four integers (``param_mul_45``, ``param_add_45``, ``param_mul_8``
and ``secret_key_8``); together with the per-string ``seed`` they fully
determine the keystream, so decryption is completely static.

Note: the ``charmap`` table the runtime builds is a no-op shuffle -- for every
``n`` in 1..256 it stores ``string.char(n-1)`` -- so ``chars[b + 1] ==
string.char(b)`` and we can ignore it entirely.
"""

from __future__ import annotations

import math

_MOD45 = 35184372088832  # 2**45


class Keystream:
    def __init__(self, param_mul_45: int, param_add_45: int,
                 param_mul_8: int, secret_key_8: int):
        self.param_mul_45 = param_mul_45
        self.param_add_45 = param_add_45
        self.param_mul_8 = param_mul_8
        self.secret_key_8 = secret_key_8
        self.state_45 = 0.0
        self.state_8 = 2.0
        self._queue = []

    def set_seed(self, seed: int):
        self.state_45 = float(seed % _MOD45)
        self.state_8 = float(seed % 255 + 2)
        self._queue = []

    @staticmethod
    def _mod(a: float, b: float) -> float:
        # Lua's `%` is floored modulo: a - floor(a/b)*b
        return a - math.floor(a / b) * b

    def _random_32(self) -> int:
        self.state_45 = self._mod(
            self.state_45 * self.param_mul_45 + self.param_add_45, _MOD45)
        while True:
            self.state_8 = self._mod(self.state_8 * self.param_mul_8, 257.0)
            if self.state_8 != 1.0:
                break
        r = self._mod(self.state_8, 32.0)
        # Lua: floor(state_45 / 2^(13-(state_8-r)/32)) % 2^32 / 2^r
        tmp = math.floor(self.state_45 / 2.0 ** (13 - (self.state_8 - r) / 32.0))
        n = self._mod(tmp, 2.0 ** 32) / 2.0 ** r
        return int(math.floor(self._mod(n, 1.0) * 2.0 ** 32) + math.floor(n))

    def next_byte(self) -> int:
        if not self._queue:
            rnd = self._random_32()
            low16 = rnd % 65536
            high16 = (rnd - low16) // 65536
            b1 = low16 % 256
            b2 = (low16 - b1) // 256
            b3 = high16 % 256
            b4 = (high16 - b3) // 256
            self._queue = [b1, b2, b3, b4]
        return self._queue.pop()   # table.remove -> pops the tail


def encrypt(plain: str, seed: int, param_mul_45: int, param_add_45: int,
            param_mul_8: int, secret_key_8: int) -> str:
    """Inverse of :func:`decrypt` -- mirrors ``EncryptStrings.encrypt`` and is
    used only by the test-suite to prove the keystream round-trips."""
    ks = Keystream(param_mul_45, param_add_45, param_mul_8, secret_key_8)
    ks.set_seed(seed)
    prev = secret_key_8
    out = bytearray()
    for byte in plain.encode("latin-1", "replace"):
        out.append((byte - (ks.next_byte() + prev)) % 256)
        prev = byte
    return out.decode("latin-1")


def decrypt(enc: str, seed: int, param_mul_45: int, param_add_45: int,
            param_mul_8: int, secret_key_8: int) -> str:
    ks = Keystream(param_mul_45, param_add_45, param_mul_8, secret_key_8)
    ks.set_seed(seed)
    prev = secret_key_8
    out = bytearray()
    data = enc.encode("latin-1", "replace")
    for byte in data:
        prev = (byte + ks.next_byte() + prev) % 256
        out.append(prev)
    return out.decode("latin-1")
