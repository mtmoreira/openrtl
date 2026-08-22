"""Bit-accurate synchronous FIFO reference model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CycleResult:
    wr_ready: bool
    rd_valid: bool
    rd_data: int
    write_accepted: bool
    read_accepted: bool
    full: bool
    empty: bool
    level: int


class SyncFifoModel:
    def __init__(self, width: int = 8, depth: int = 4) -> None:
        if width < 1 or depth < 2:
            raise ValueError("FIFO width and depth are out of range")
        self.width = width
        self.depth = depth
        self._mask = (1 << width) - 1
        self._queue: list[int] = []

    def cycle(
        self,
        *,
        rst_n: bool = True,
        wr_valid: bool = False,
        wr_data: int = 0,
        rd_ready: bool = False,
    ) -> CycleResult:
        if not rst_n:
            self._queue.clear()
            return CycleResult(False, False, 0, False, False, False, True, 0)
        rd_valid = bool(self._queue)
        rd_data = self._queue[0] if rd_valid else 0
        read_accepted = rd_valid and rd_ready
        wr_ready = len(self._queue) < self.depth or read_accepted
        write_accepted = wr_valid and wr_ready
        if read_accepted:
            self._queue.pop(0)
        if write_accepted:
            self._queue.append(wr_data & self._mask)
        level = len(self._queue)
        return CycleResult(
            wr_ready,
            rd_valid,
            rd_data,
            write_accepted,
            read_accepted,
            level == self.depth,
            level == 0,
            level,
        )

    @property
    def contents(self) -> tuple[int, ...]:
        return tuple(self._queue)
