"""Version-independent deterministic RNG (HMAC-SHA256 counter mode).

Python's ``random`` is not used: its algorithms are not a stable contract across
versions. Each domain ("wild", "trainers", ...) gets an independent stream
derived from the build key, so changing one feature never perturbs another.
"""

from __future__ import annotations

import hashlib
import hmac


class Stream:
    def __init__(self, key: bytes, domain: str) -> None:
        self._key = hmac.new(key, b"b2er-stream|" + domain.encode("utf-8"), hashlib.sha256).digest()
        self._counter = 0
        self._buffer = b""

    def _bytes(self, n: int) -> bytes:
        while len(self._buffer) < n:
            self._buffer += hmac.new(self._key, self._counter.to_bytes(8, "big"), hashlib.sha256).digest()
            self._counter += 1
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def below(self, n: int) -> int:
        """Uniform integer in [0, n) by rejection sampling."""
        if n <= 0:
            raise ValueError("n must be positive")
        if n == 1:
            return 0
        width = (n - 1).bit_length()
        nbytes = (width + 7) // 8
        mask = (1 << width) - 1
        while True:
            value = int.from_bytes(self._bytes(nbytes), "big") & mask
            if value < n:
                return value

    def choice(self, items):
        items = list(items)
        if not items:
            raise ValueError("choice from an empty sequence")
        return items[self.below(len(items))]

    def shuffle(self, items: list) -> list:
        result = list(items)
        for i in range(len(result) - 1, 0, -1):
            j = self.below(i + 1)
            result[i], result[j] = result[j], result[i]
        return result

    def fork(self, label: str) -> "Stream":
        return Stream(self._key, label)
