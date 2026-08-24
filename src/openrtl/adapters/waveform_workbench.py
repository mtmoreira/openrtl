"""Bounded VCD inspection and deterministic Surfer focus collateral."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openrtl.adapters.waveforms import SignalTransition, VcdIndex, WaveformFocus


MAX_VCD_BYTES = 64 * 1024 * 1024
MAX_REPORT_TRANSITIONS = 10_000


@dataclass(frozen=True)
class SignalInspection:
    name: str
    value_at_start: str | None
    transitions: tuple[SignalTransition, ...]
    truncated: bool

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transitions": [
                {"timestamp_fs": item.timestamp_fs, "value": item.value}
                for item in self.transitions
            ],
            "truncated": self.truncated,
            "value_at_start": self.value_at_start,
        }


@dataclass(frozen=True)
class WaveformInspection:
    trace: str
    timescale_fs: int
    trace_end_fs: int
    start_fs: int
    end_fs: int
    signal_names: tuple[str, ...]
    selected_signals: tuple[SignalInspection, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "end_fs": self.end_fs,
            "schema": "openrtl.waveform-inspection.v1",
            "selected_signals": [item.payload() for item in self.selected_signals],
            "signal_names": self.signal_names,
            "start_fs": self.start_fs,
            "timescale_fs": self.timescale_fs,
            "trace": self.trace,
            "trace_end_fs": self.trace_end_fs,
        }


def inspect_vcd(
    root: Path,
    trace: Path,
    *,
    signals: tuple[str, ...] = (),
    start_fs: int = 0,
    end_fs: int | None = None,
    max_transitions: int = 200,
) -> tuple[VcdIndex, WaveformInspection]:
    """Load a contained VCD and return a bounded, reviewable inspection."""

    if isinstance(max_transitions, bool) or not (
        1 <= max_transitions <= MAX_REPORT_TRANSITIONS
    ):
        raise ValueError("max_transitions must be between 1 and 10000")
    if len(set(signals)) != len(signals):
        raise ValueError("selected waveform signals must be unique")
    resolved_root = root.resolve(strict=True)
    if resolved_root == Path("/"):
        raise ValueError("waveform root must be bounded")
    candidate = trace if trace.is_absolute() else resolved_root / trace
    if candidate.is_symlink():
        raise ValueError("waveform trace must not be a symlink")
    resolved_trace = candidate.resolve(strict=True)
    try:
        relative_trace = resolved_trace.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("waveform trace must be contained by its root") from error
    if not resolved_trace.is_file():
        raise ValueError("waveform trace must be a regular file")
    size = resolved_trace.stat().st_size
    if size < 1 or size > MAX_VCD_BYTES:
        raise ValueError("waveform trace size exceeds its bound")
    try:
        content = resolved_trace.read_text(encoding="utf-8")
        index = VcdIndex.parse(content)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("waveform trace is not a valid bounded VCD") from error
    selected_end = index.end_time_fs if end_fs is None else end_fs
    if start_fs < 0 or selected_end < start_fs or selected_end > index.end_time_fs:
        raise ValueError("waveform inspection interval is invalid")

    selected: list[SignalInspection] = []
    for signal in signals:
        all_transitions = index.transitions(signal, start_fs, selected_end)
        selected.append(
            SignalInspection(
                name=signal,
                value_at_start=index.value_at(signal, start_fs),
                transitions=all_transitions[:max_transitions],
                truncated=len(all_transitions) > max_transitions,
            )
        )
    return index, WaveformInspection(
        trace=relative_trace.as_posix(),
        timescale_fs=index.timescale_fs,
        trace_end_fs=index.end_time_fs,
        start_fs=start_fs,
        end_fs=selected_end,
        signal_names=index.signal_names,
        selected_signals=tuple(selected),
    )


def surfer_command_file(focus: WaveformFocus) -> str:
    """Render stable Surfer commands for the selected signals and interval."""

    commands = [f"variable_add {signal}" for signal in focus.signals]
    commands.append(f"zoom_to {focus.start_fs}fs {focus.end_fs}fs")
    commands.append(f"cursor_set {focus.start_fs}fs")
    commands.append(f"marker_set_at {focus.start_fs}fs focus-start")
    commands.append(f"marker_set_at {focus.end_fs}fs focus-end")
    return "\n".join(commands) + "\n"
