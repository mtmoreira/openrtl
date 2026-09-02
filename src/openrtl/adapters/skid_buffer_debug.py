"""Ready/valid skid-buffer edge analysis over a bounded VCD trace."""

from __future__ import annotations

import hashlib
from pathlib import Path

from openrtl.adapters.waveform_workbench import inspect_vcd
from openrtl.adapters.waveforms import VcdIndex
from openrtl.application.debugging import (
    DebugFinding,
    DebugObservation,
    DebugSessionReport,
    DebugSeverity,
)
from openrtl.domain import SourceAnchor, WaveformAnchor


MAX_SKID_DEBUG_EDGES = 10_000


def analyze_skid_buffer_waveform(
    root: Path,
    trace: Path,
    *,
    start_fs: int = 0,
    end_fs: int | None = None,
    hierarchy: str = "skid_buffer",
    rtl_path: Path | None = None,
) -> DebugSessionReport:
    """Explain skid-buffer handshakes and flag edge-local contract violations."""

    signals = _signals(hierarchy)
    index, inspection = inspect_vcd(
        root,
        trace,
        signals=signals,
        start_fs=start_fs,
        end_fs=end_fs,
        max_transitions=1,
    )
    if inspection.end_fs <= inspection.start_fs:
        raise ValueError("skid-buffer debug interval must contain time")
    resolved_root = root.resolve(strict=True)
    resolved_trace = (resolved_root / inspection.trace).resolve(strict=True)
    trace_sha256 = hashlib.sha256(resolved_trace.read_bytes()).hexdigest()
    token = hashlib.sha256(
        f"{trace_sha256}:{inspection.start_fs}:{inspection.end_fs}:{hierarchy}".encode()
    ).hexdigest()[:16]
    session_id = f"debug.skid.{token}"
    trace_id = f"{session_id}.trace"
    source_anchors = _source_anchors(resolved_root, rtl_path)

    rising_edges = tuple(
        item.timestamp_fs
        for item in index.transitions(f"{hierarchy}.clk", 0, inspection.end_fs)
        if item.value == "1" and item.timestamp_fs > 0
    )
    selected_edges = tuple(
        value for value in rising_edges if inspection.start_fs <= value <= inspection.end_fs
    )
    if not selected_edges:
        raise ValueError("skid-buffer debug interval contains no rising clock edge")
    if len(selected_edges) > MAX_SKID_DEBUG_EDGES:
        raise ValueError("skid-buffer debug edge count exceeds its bound")

    observations: list[DebugObservation] = []
    findings: list[DebugFinding] = []

    def add_finding(
        timestamp_fs: int,
        category: str,
        requirement_id: str,
        summary: str,
        expected: str,
        observed: str,
        next_action: str,
        relevant_signals: tuple[str, ...],
    ) -> None:
        anchor_start = max(inspection.start_fs, timestamp_fs - index.timescale_fs)
        anchor_end = min(inspection.end_fs, timestamp_fs + index.timescale_fs)
        if anchor_end <= anchor_start:
            raise ValueError("skid-buffer finding has no visible waveform interval")
        findings.append(
            DebugFinding(
                f"{session_id}.{category}.{timestamp_fs}",
                DebugSeverity.ERROR,
                requirement_id,
                summary,
                expected,
                observed,
                next_action,
                WaveformAnchor(
                    trace_id,
                    anchor_start,
                    anchor_end,
                    relevant_signals,
                    (timestamp_fs,),
                ),
            )
        )

    for timestamp_fs in selected_edges:
        rst_n = _bit_before(index, f"{hierarchy}.rst_n", timestamp_fs)
        if not rst_n:
            full_after = _bit_at(index, f"{hierarchy}.full", timestamp_fs)
            if full_after:
                add_finding(
                    timestamp_fs,
                    "reset",
                    "skid.reset",
                    "Reset did not clear retained skid state.",
                    "0",
                    "1",
                    "Inspect the synchronous active-low reset branch.",
                    (f"{hierarchy}.rst_n", f"{hierarchy}.full"),
                )
            observations.append(
                DebugObservation(
                    f"{session_id}.edge.{timestamp_fs}",
                    timestamp_fs,
                    "reset-edge",
                    "Reset cleared retained skid state.",
                    ("skid.reset",),
                    (("rst_n", "0"), ("occupied_after", str(int(full_after)))),
                )
            )
            continue
        s_valid = _bit_before(index, f"{hierarchy}.s_valid", timestamp_fs)
        s_ready = _bit_before(index, f"{hierarchy}.s_ready", timestamp_fs)
        m_valid = _bit_before(index, f"{hierarchy}.m_valid", timestamp_fs)
        m_ready = _bit_before(index, f"{hierarchy}.m_ready", timestamp_fs)
        full_before = _bit_before(index, f"{hierarchy}.full", timestamp_fs)
        full_after = _bit_at(index, f"{hierarchy}.full", timestamp_fs)
        input_internal = _bit_before(
            index, f"{hierarchy}.input_accepted", timestamp_fs
        )
        output_internal = _bit_before(
            index, f"{hierarchy}.output_accepted", timestamp_fs
        )
        input_accepted = s_valid and s_ready
        output_accepted = m_valid and m_ready
        s_data = _integer_before(index, f"{hierarchy}.s_data", timestamp_fs)
        m_data = _integer_before(index, f"{hierarchy}.m_data", timestamp_fs)
        data_q = (
            _integer_before(index, f"{hierarchy}.data_q", timestamp_fs)
            if full_before
            else 0
        )

        expected_ready = (not full_before) or m_ready
        if s_ready != expected_ready:
            add_finding(
                timestamp_fs,
                "refill-ready",
                "skid.refill",
                "Input readiness prevents a same-edge replacement transfer.",
                str(int(expected_ready)),
                str(int(s_ready)),
                "Inspect the full-buffer output-ready bypass in the s_ready assignment.",
                (
                    f"{hierarchy}.full",
                    f"{hierarchy}.m_ready",
                    f"{hierarchy}.s_valid",
                    f"{hierarchy}.s_ready",
                ),
            )
        if m_valid != (full_before or s_valid):
            add_finding(
                timestamp_fs,
                "output-valid",
                "skid.accept",
                "Output validity disagrees with retained or incoming data.",
                str(int(full_before or s_valid)),
                str(int(m_valid)),
                "Inspect the retained-state and pass-through m_valid expression.",
                (f"{hierarchy}.full", f"{hierarchy}.s_valid", f"{hierarchy}.m_valid"),
            )
        if input_internal != input_accepted:
            add_finding(
                timestamp_fs,
                "input-handshake",
                "skid.accept",
                "Internal input acceptance disagrees with valid/ready.",
                str(int(input_accepted)),
                str(int(input_internal)),
                "Inspect s_valid, s_ready, and input_accepted before this edge.",
                (f"{hierarchy}.s_valid", f"{hierarchy}.s_ready", f"{hierarchy}.input_accepted"),
            )
        if output_internal != output_accepted:
            add_finding(
                timestamp_fs,
                "output-handshake",
                "skid.accept",
                "Internal output acceptance disagrees with valid/ready.",
                str(int(output_accepted)),
                str(int(output_internal)),
                "Inspect m_valid, m_ready, and output_accepted before this edge.",
                (f"{hierarchy}.m_valid", f"{hierarchy}.m_ready", f"{hierarchy}.output_accepted"),
            )
        if full_before and m_data != data_q:
            add_finding(
                timestamp_fs,
                "retained-data",
                "skid.order",
                "Output data does not match the retained skid word.",
                _hex(data_q),
                _hex(m_data),
                "Inspect the full-state output mux and retained data register.",
                (f"{hierarchy}.full", f"{hierarchy}.data_q", f"{hierarchy}.m_data"),
            )

        expected_full = _expected_full(
            rst_n,
            full_before,
            input_accepted,
            output_accepted,
        )
        if full_after != expected_full:
            add_finding(
                timestamp_fs,
                "occupancy",
                "skid.backpressure",
                "Post-edge occupancy disagrees with accepted transfers.",
                str(int(expected_full)),
                str(int(full_after)),
                "Inspect the accepted-transfer state update case.",
                (
                    f"{hierarchy}.input_accepted",
                    f"{hierarchy}.output_accepted",
                    f"{hierarchy}.full",
                ),
            )

        event, summary, requirements = _event_summary(
            rst_n,
            input_accepted,
            output_accepted,
            full_before,
            full_after,
        )
        observations.append(
            DebugObservation(
                f"{session_id}.edge.{timestamp_fs}",
                timestamp_fs,
                event,
                summary,
                requirements,
                (
                    ("rst_n", str(int(rst_n))),
                    ("s_valid", str(int(s_valid))),
                    ("s_ready", str(int(s_ready))),
                    ("s_data", _hex(s_data)),
                    ("m_valid", str(int(m_valid))),
                    ("m_ready", str(int(m_ready))),
                    ("m_data", _hex(m_data)),
                    ("occupied_before", str(int(full_before))),
                    ("occupied_after", str(int(full_after))),
                ),
            )
        )

    anchor = WaveformAnchor(
        trace_id,
        inspection.start_fs,
        inspection.end_fs,
        signals,
        selected_edges,
    )
    return DebugSessionReport(
        session_id,
        "ready-valid.skid-buffer",
        inspection.trace,
        f"sha256:{trace_sha256}",
        index.timescale_fs,
        anchor,
        source_anchors,
        (("edge_count", str(len(observations))), ("hierarchy", hierarchy)),
        tuple(observations),
        tuple(findings),
    )


def _expected_full(
    rst_n: bool,
    full_before: bool,
    input_accepted: bool,
    output_accepted: bool,
) -> bool:
    if not rst_n:
        return False
    if input_accepted and output_accepted:
        return full_before
    if input_accepted:
        return True
    if output_accepted:
        return False
    return full_before


def _signals(hierarchy: str) -> tuple[str, ...]:
    if not hierarchy or any(value.isspace() for value in hierarchy):
        raise ValueError("skid-buffer hierarchy must be non-empty and contain no whitespace")
    return tuple(
        f"{hierarchy}.{name}"
        for name in (
            "clk",
            "rst_n",
            "s_valid",
            "s_ready",
            "s_data",
            "m_valid",
            "m_ready",
            "m_data",
            "occupied",
            "full",
            "data_q",
            "input_accepted",
            "output_accepted",
        )
    )


def _source_anchors(root: Path, rtl_path: Path | None) -> tuple[SourceAnchor, ...]:
    if rtl_path is None:
        return ()
    candidate = rtl_path if rtl_path.is_absolute() else root / rtl_path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.is_symlink():
        raise ValueError("skid-buffer RTL source is outside the repository")
    content = resolved.read_bytes()
    if not content or len(content) > 1024 * 1024:
        raise ValueError("skid-buffer RTL source size is invalid")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("skid-buffer RTL source is not UTF-8") from error
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    snippets = ("assign s_ready", "assign m_valid", "assign m_data", "unique case")
    ranges = tuple(
        (index, index)
        for index, line in enumerate(lines, start=1)
        if any(value in line for value in snippets)
    )
    if len(ranges) != len(snippets):
        raise ValueError("skid-buffer RTL source lacks exact debug anchor points")
    relative = resolved.relative_to(root).as_posix()
    return tuple(SourceAnchor(relative, start, end, digest) for start, end in ranges)


def _bit_before(index: VcdIndex, signal: str, timestamp_fs: int) -> bool:
    return bool(_integer(index.value_before(signal, timestamp_fs), signal))


def _bit_at(index: VcdIndex, signal: str, timestamp_fs: int) -> bool:
    return bool(_integer(index.value_at(signal, timestamp_fs), signal))


def _integer_before(index: VcdIndex, signal: str, timestamp_fs: int) -> int:
    return _integer(index.value_before(signal, timestamp_fs), signal)


def _integer(value: str | None, signal: str) -> int:
    if value is None or not value or any(bit not in "01" for bit in value):
        raise ValueError(f"skid-buffer debug signal is unknown at a sampled edge: {signal}")
    return int(value, 2)


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _event_summary(
    rst_n: bool,
    input_accepted: bool,
    output_accepted: bool,
    full_before: bool,
    full_after: bool,
) -> tuple[str, str, tuple[str, ...]]:
    if not rst_n:
        return "reset-edge", "Reset cleared retained skid state.", ("skid.reset",)
    if input_accepted and output_accepted:
        return (
            "simultaneous-transfer",
            f"Input and output transferred; occupancy {int(full_before)} -> {int(full_after)}.",
            ("skid.accept", "skid.refill", "skid.order"),
        )
    if input_accepted:
        return (
            "input-transfer",
            f"Input transferred; occupancy {int(full_before)} -> {int(full_after)}.",
            ("skid.accept", "skid.backpressure"),
        )
    if output_accepted:
        return (
            "output-transfer",
            f"Output transferred; occupancy {int(full_before)} -> {int(full_after)}.",
            ("skid.accept", "skid.order"),
        )
    return (
        "idle-edge",
        f"No transfer; occupancy remained {int(full_before)} -> {int(full_after)}.",
        ("skid.backpressure",),
    )
