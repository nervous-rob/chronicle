"""chronicle.rng — per-process counter-based random streams (ECOLOGY F3, ERRATA #30).

Every draw is keyed (seed, day, process, entity) on numpy's Philox.  Two runs that differ in one
setting produce identical draws for every process the setting does not touch, so runs are diffable
and a new process never renumbers an old one.

Contract
  one(process, day, entity) -> Generator for a single entity (a scene, a journey, a rare event).
  vec(process, day)         -> Generator for a whole-array pass.  Callers MUST index draws by a
                               stable id order (cell index, person id) and draw a fixed length
                               (max_id + 1), never len(alive); otherwise a death reshuffles draws.
Process ids are a hash of the process name, not a registration order.  Day is the absolute day
index of the run (0 = first day); world generation uses day 0 with process "terrain".
"""
from __future__ import annotations

import hashlib
import numpy as np

LAYOUT_VERSION = 2  # v1 adjacent-day blocks overlapped
_MASK64 = (1 << 64) - 1
_VEC_ENTITY = (1 << 63)          # reserved entity slot for vectorized passes


def process_id(name: str) -> int:
    return int.from_bytes(hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest(), "little")


class Streams:
    __slots__ = ("seed",)

    def __init__(self, seed: int):
        self.seed = int(seed) & _MASK64

    def one(self, process: str, day: int, entity: int = 0) -> np.random.Generator:
        if not 0 <= day <= _MASK64 or not 0 <= entity <= _MASK64:
            raise ValueError("day and entity must fit unsigned 64-bit coordinates")
        key = np.array([self.seed, process_id(process)], dtype=np.uint64)
        # Low 128 bits are the advancing draw/block index, NOT the day.
        # Reserve 2**128 Philox blocks per (day, entity).
        counter = np.array([0, 0, int(day), int(entity)], dtype=np.uint64)
        return np.random.Generator(np.random.Philox(key=key, counter=counter))

    def vec(self, process: str, day: int) -> np.random.Generator:
        return self.one(process, day, _VEC_ENTITY)

    def uniform(self, process: str, day: int, n: int) -> np.ndarray:
        """Fixed-length U[0,1) vector for an id-indexed pass (see contract)."""
        return self.vec(process, day).random(n)
