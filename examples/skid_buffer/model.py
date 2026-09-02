"""Cycle-accurate reference model for a one-entry ready/valid skid buffer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkidCycle:
    s_ready: bool
    m_valid: bool
    m_data: int
    input_accepted: bool
    output_accepted: bool
    occupied_after: bool


class SkidBufferModel:
    """Model transparent transfer, backpressure capture, and full-rate refill."""

    def __init__(self, *, width: int) -> None:
        if isinstance(width, bool) or width < 1:
            raise ValueError("width must be positive")
        self.width = width
        self._mask = (1 << width) - 1
        self.occupied = False
        self.retained_data = 0

    def cycle(
        self,
        *,
        rst_n: bool = True,
        s_valid: bool = False,
        s_data: int = 0,
        m_ready: bool = False,
    ) -> SkidCycle:
        if isinstance(s_data, bool) or s_data < 0 or s_data > self._mask:
            raise ValueError("s_data is outside the configured width")
        s_ready = (not self.occupied) or m_ready
        m_valid = self.occupied or s_valid
        m_data = self.retained_data if self.occupied else s_data
        input_accepted = bool(s_valid and s_ready)
        output_accepted = bool(m_valid and m_ready)

        if not rst_n:
            self.occupied = False
            self.retained_data = 0
        elif input_accepted and not output_accepted:
            self.occupied = True
            self.retained_data = s_data
        elif output_accepted and not input_accepted:
            self.occupied = False
        elif input_accepted and output_accepted and self.occupied:
            self.occupied = True
            self.retained_data = s_data

        return SkidCycle(
            s_ready,
            m_valid,
            m_data,
            input_accepted,
            output_accepted,
            self.occupied,
        )
