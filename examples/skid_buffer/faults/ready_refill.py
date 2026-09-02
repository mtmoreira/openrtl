"""Deterministic VCD fixture for the full-buffer refill-ready invariant."""

from __future__ import annotations


_SIGNALS: tuple[tuple[str, str, int], ...] = (
    ("!", "clk", 1),
    ('"', "rst_n", 1),
    ("#", "s_valid", 1),
    ("$", "s_ready", 1),
    ("%", "s_data", 8),
    ("&", "m_valid", 1),
    ("'", "m_ready", 1),
    ("(", "m_data", 8),
    (")", "occupied", 1),
    ("*", "full", 1),
    ("+", "data_q", 8),
    (",", "input_accepted", 1),
    ("-", "output_accepted", 1),
)


def render_skid_buffer_trace(*, refill_ready_fault: bool) -> str:
    """Render matching before/after traces with one refill-readiness difference."""

    states = _states(refill_ready_fault)
    lines = [
        "$date deterministic $end",
        "$version openrtl-skid-buffer-fixture $end",
        "$timescale 1ns $end",
        "$scope module skid_buffer $end",
    ]
    for identifier, name, width in _SIGNALS:
        lines.append(f"$var wire {width} {identifier} {name} $end")
    lines.extend(("$upscope $end", "$enddefinitions $end"))
    previous: dict[str, int] = {}
    for timestamp, state in states:
        lines.append(f"#{timestamp}")
        for identifier, name, width in _SIGNALS:
            value = state[name]
            if previous.get(name) == value:
                continue
            lines.append(
                f"{value}{identifier}"
                if width == 1
                else f"b{value:0{width}b} {identifier}"
            )
            previous[name] = value
    return "\n".join(lines) + "\n"


def _states(refill_ready_fault: bool) -> tuple[tuple[int, dict[str, int]], ...]:
    base = {
        "clk": 0,
        "rst_n": 0,
        "s_valid": 0,
        "s_ready": 1,
        "s_data": 0,
        "m_valid": 0,
        "m_ready": 0,
        "m_data": 0,
        "occupied": 0,
        "full": 0,
        "data_q": 0,
        "input_accepted": 0,
        "output_accepted": 0,
    }
    states: list[tuple[int, dict[str, int]]] = []

    def add(timestamp: int, **changes: int) -> None:
        current = dict(states[-1][1] if states else base)
        current.update(changes)
        states.append((timestamp, current))

    add(0)
    add(5, clk=1)
    add(
        10,
        clk=0,
        rst_n=1,
        s_valid=1,
        s_ready=1,
        s_data=0x11,
        m_valid=1,
        m_data=0x11,
        input_accepted=1,
    )
    add(
        15,
        clk=1,
        s_ready=0,
        m_data=0x11,
        occupied=1,
        full=1,
        data_q=0x11,
        input_accepted=0,
    )
    add(
        20,
        clk=0,
        s_valid=1,
        s_ready=0 if refill_ready_fault else 1,
        s_data=0x22,
        m_valid=1,
        m_ready=1,
        m_data=0x11,
        input_accepted=0 if refill_ready_fault else 1,
        output_accepted=1,
    )
    add(
        25,
        clk=1,
        s_ready=1,
        m_data=0x22,
        occupied=0 if refill_ready_fault else 1,
        full=0 if refill_ready_fault else 1,
        data_q=0x11 if refill_ready_fault else 0x22,
        input_accepted=1,
        output_accepted=1,
    )
    add(
        26,
        s_valid=0,
        s_data=0,
        m_valid=0 if refill_ready_fault else 1,
        m_data=0 if refill_ready_fault else 0x22,
        input_accepted=0,
        output_accepted=0 if refill_ready_fault else 1,
    )
    add(30, clk=0)
    add(
        35,
        clk=1,
        m_valid=0,
        m_data=0,
        occupied=0,
        full=0,
        output_accepted=0,
    )
    add(40, clk=0)
    return tuple(states)
