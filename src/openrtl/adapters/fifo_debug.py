"""Synchronous-FIFO edge and invariant analysis over a bounded VCD."""

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


MAX_DEBUG_EDGES = 10_000


def analyze_fifo_waveform(
    root: Path,
    trace: Path,
    *,
    start_fs: int = 0,
    end_fs: int | None = None,
    depth: int | None = None,
    hierarchy: str = "sync_fifo",
    rtl_path: Path | None = None,
) -> DebugSessionReport:
    """Explain FIFO transfers and flag contract violations at rising edges."""

    signals = _fifo_signals(hierarchy)
    index, inspection = inspect_vcd(
        root,
        trace,
        signals=signals,
        start_fs=start_fs,
        end_fs=end_fs,
        max_transitions=1,
    )
    if inspection.end_fs <= inspection.start_fs:
        raise ValueError("FIFO debug interval must contain time")
    resolved_trace = (root.resolve(strict=True) / inspection.trace).resolve(strict=True)
    content = resolved_trace.read_bytes()
    trace_sha256 = hashlib.sha256(content).hexdigest()
    selected_depth = _depth(index, hierarchy, depth)
    session_token = hashlib.sha256(
        f"{trace_sha256}:{inspection.start_fs}:{inspection.end_fs}:{hierarchy}".encode()
    ).hexdigest()[:16]
    session_id = f"debug.fifo.{session_token}"
    trace_id = f"{session_id}.trace"
    source_anchors = _source_anchors(root, rtl_path)

    clock = f"{hierarchy}.clk"
    rising_edges = tuple(
        item.timestamp_fs
        for item in index.transitions(clock, 0, inspection.end_fs)
        if item.value == "1" and item.timestamp_fs > 0
    )
    if len(rising_edges) > MAX_DEBUG_EDGES:
        raise ValueError("FIFO debug edge count exceeds its bound")
    selected_edges = tuple(
        value
        for value in rising_edges
        if inspection.start_fs <= value <= inspection.end_fs
    )
    if not selected_edges:
        raise ValueError("FIFO debug interval contains no rising clock edge")

    observations: list[DebugObservation] = []
    findings: list[DebugFinding] = []
    queued_words: list[int] = []
    queue_known = False

    def add_finding(
        timestamp_fs: int,
        kind: str,
        requirement_id: str,
        summary: str,
        expected: str,
        observed: str,
        next_action: str,
        relevant_signals: tuple[str, ...],
    ) -> None:
        if timestamp_fs < inspection.start_fs:
            return
        anchor_start = max(inspection.start_fs, timestamp_fs - index.timescale_fs)
        anchor_end = min(inspection.end_fs, timestamp_fs + index.timescale_fs)
        if anchor_end <= anchor_start:
            anchor_end = timestamp_fs
            anchor_start = max(inspection.start_fs, timestamp_fs - index.timescale_fs)
        findings.append(
            DebugFinding(
                f"{session_id}.{kind}.{timestamp_fs}",
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

    for timestamp_fs in rising_edges:
        rst_n = _bit_before(index, f"{hierarchy}.rst_n", timestamp_fs)
        wr_valid = _bit_before(index, f"{hierarchy}.wr_valid", timestamp_fs)
        wr_ready = _bit_before(index, f"{hierarchy}.wr_ready", timestamp_fs)
        rd_valid = _bit_before(index, f"{hierarchy}.rd_valid", timestamp_fs)
        rd_ready = _bit_before(index, f"{hierarchy}.rd_ready", timestamp_fs)
        write_internal = _bit_before(
            index,
            f"{hierarchy}.write_accepted",
            timestamp_fs,
        )
        read_internal = _bit_before(
            index,
            f"{hierarchy}.read_accepted",
            timestamp_fs,
        )
        write_accepted = wr_valid and wr_ready
        read_accepted = rd_valid and rd_ready
        level_before = _integer_before(index, f"{hierarchy}.level", timestamp_fs)
        level_after = _integer_at(index, f"{hierarchy}.level", timestamp_fs)
        full_before = _bit_before(index, f"{hierarchy}.full", timestamp_fs)
        full_after = _bit_at(index, f"{hierarchy}.full", timestamp_fs)
        empty_before = _bit_before(index, f"{hierarchy}.empty", timestamp_fs)
        empty_after = _bit_at(index, f"{hierarchy}.empty", timestamp_fs)
        write_pointer_before = _integer_before(
            index,
            f"{hierarchy}.write_pointer",
            timestamp_fs,
        )
        write_pointer_after = _integer_at(
            index,
            f"{hierarchy}.write_pointer",
            timestamp_fs,
        )
        read_pointer_before = _integer_before(
            index,
            f"{hierarchy}.read_pointer",
            timestamp_fs,
        )
        read_pointer_after = _integer_at(
            index,
            f"{hierarchy}.read_pointer",
            timestamp_fs,
        )
        write_data = _integer_before(index, f"{hierarchy}.wr_data", timestamp_fs)
        read_data = _integer_before(index, f"{hierarchy}.rd_data", timestamp_fs)

        if not queue_known and level_before == 0:
            queued_words = []
            queue_known = True

        if write_internal != write_accepted:
            add_finding(
                timestamp_fs,
                "write-handshake",
                "fifo.write",
                "Internal write acceptance disagrees with valid/ready.",
                str(int(write_accepted)),
                str(int(write_internal)),
                "Inspect wr_valid, wr_ready, and write_accepted before this edge.",
                (
                    f"{hierarchy}.clk",
                    f"{hierarchy}.wr_valid",
                    f"{hierarchy}.wr_ready",
                    f"{hierarchy}.write_accepted",
                ),
            )
        if read_internal != read_accepted:
            add_finding(
                timestamp_fs,
                "read-handshake",
                "fifo.read",
                "Internal read acceptance disagrees with valid/ready.",
                str(int(read_accepted)),
                str(int(read_internal)),
                "Inspect rd_valid, rd_ready, and read_accepted before this edge.",
                (
                    f"{hierarchy}.clk",
                    f"{hierarchy}.rd_valid",
                    f"{hierarchy}.rd_ready",
                    f"{hierarchy}.read_accepted",
                ),
            )

        expected_ready = (not full_before) or read_accepted
        if wr_ready != expected_ready:
            add_finding(
                timestamp_fs,
                "backpressure",
                "fifo.backpressure",
                "Write backpressure violates the full/read-through contract.",
                str(int(expected_ready)),
                str(int(wr_ready)),
                "Inspect full, rd_valid, rd_ready, and the wr_ready combinational path.",
                (
                    f"{hierarchy}.full",
                    f"{hierarchy}.rd_valid",
                    f"{hierarchy}.rd_ready",
                    f"{hierarchy}.wr_ready",
                ),
            )
        if rd_valid != (not empty_before):
            add_finding(
                timestamp_fs,
                "read-valid",
                "fifo.status",
                "Read validity disagrees with FIFO emptiness.",
                str(int(not empty_before)),
                str(int(rd_valid)),
                "Inspect empty, count, and the rd_valid assignment.",
                (
                    f"{hierarchy}.empty",
                    f"{hierarchy}.level",
                    f"{hierarchy}.rd_valid",
                ),
            )

        if not rst_n:
            expected_level = 0
            expected_write_pointer = 0
            expected_read_pointer = 0
            queued_words = []
            queue_known = True
        else:
            expected_level = level_before + int(write_accepted) - int(read_accepted)
            expected_write_pointer = (
                (write_pointer_before + 1) % selected_depth
                if write_accepted
                else write_pointer_before
            )
            expected_read_pointer = (
                (read_pointer_before + 1) % selected_depth
                if read_accepted
                else read_pointer_before
            )

        if level_after != expected_level:
            requirement_id = (
                "fifo.simultaneous"
                if write_accepted and read_accepted
                else "fifo.write"
                if write_accepted
                else "fifo.read"
                if read_accepted
                else "fifo.status"
            )
            add_finding(
                timestamp_fs,
                "level",
                requirement_id,
                "FIFO level does not match accepted transfers.",
                str(expected_level),
                str(level_after),
                "Inspect the count update case and both acceptance signals.",
                (
                    f"{hierarchy}.write_accepted",
                    f"{hierarchy}.read_accepted",
                    f"{hierarchy}.level",
                ),
            )
        if full_after != (level_after == selected_depth):
            add_finding(
                timestamp_fs,
                "full",
                "fifo.status",
                "Full flag disagrees with the post-edge level.",
                str(int(level_after == selected_depth)),
                str(int(full_after)),
                "Inspect the full comparison and level width.",
                (f"{hierarchy}.level", f"{hierarchy}.full"),
            )
        if empty_after != (level_after == 0):
            add_finding(
                timestamp_fs,
                "empty",
                "fifo.status",
                "Empty flag disagrees with the post-edge level.",
                str(int(level_after == 0)),
                str(int(empty_after)),
                "Inspect the empty comparison and count reset path.",
                (f"{hierarchy}.level", f"{hierarchy}.empty"),
            )
        if write_pointer_after != expected_write_pointer:
            add_finding(
                timestamp_fs,
                "write-pointer",
                "fifo.wrap" if write_accepted else "fifo.write",
                "Write pointer movement disagrees with write acceptance.",
                str(expected_write_pointer),
                str(write_pointer_after),
                "Inspect write-pointer enable and wraparound logic.",
                (
                    f"{hierarchy}.write_accepted",
                    f"{hierarchy}.write_pointer",
                ),
            )
        if read_pointer_after != expected_read_pointer:
            add_finding(
                timestamp_fs,
                "read-pointer",
                "fifo.wrap" if read_accepted else "fifo.read",
                "Read pointer movement disagrees with read acceptance.",
                str(expected_read_pointer),
                str(read_pointer_after),
                "Inspect read-pointer enable and wraparound logic.",
                (
                    f"{hierarchy}.read_accepted",
                    f"{hierarchy}.read_pointer",
                ),
            )

        if rst_n and read_accepted and queue_known:
            if not queued_words:
                add_finding(
                    timestamp_fs,
                    "underflow",
                    "fifo.read",
                    "A read was accepted without a modeled retained word.",
                    "retained word available",
                    "modeled queue empty",
                    "Inspect empty, rd_valid, and read acceptance.",
                    (
                        f"{hierarchy}.empty",
                        f"{hierarchy}.rd_valid",
                        f"{hierarchy}.read_accepted",
                    ),
                )
            else:
                expected_data = queued_words.pop(0)
                if read_data != expected_data:
                    add_finding(
                        timestamp_fs,
                        "ordering",
                        "fifo.order",
                        "Read data is not the oldest modeled retained word.",
                        _hex(expected_data),
                        _hex(read_data),
                        "Inspect memory writes, read-pointer selection, and simultaneous transfers.",
                        (
                            f"{hierarchy}.rd_data",
                            f"{hierarchy}.read_pointer",
                            f"{hierarchy}.write_pointer",
                        ),
                    )
        if rst_n and write_accepted and queue_known:
            queued_words.append(write_data)
        if not queue_known and level_after == 0:
            queued_words = []
            queue_known = True

        if timestamp_fs < inspection.start_fs:
            continue
        event, summary, requirements = _event_summary(
            rst_n,
            write_accepted,
            read_accepted,
            wr_valid and not wr_ready,
            rd_ready and not rd_valid,
            level_before,
            level_after,
        )
        observations.append(
            DebugObservation(
                f"{session_id}.edge.{timestamp_fs}",
                timestamp_fs,
                event,
                summary,
                requirements,
                (
                    ("wr_valid", str(int(wr_valid))),
                    ("wr_ready", str(int(wr_ready))),
                    ("write_accepted", str(int(write_accepted))),
                    ("wr_data", _hex(write_data)),
                    ("rd_valid", str(int(rd_valid))),
                    ("rd_ready", str(int(rd_ready))),
                    ("read_accepted", str(int(read_accepted))),
                    ("rd_data", _hex(read_data)),
                    ("level_before", str(level_before)),
                    ("level_after", str(level_after)),
                    ("full_after", str(int(full_after))),
                    ("empty_after", str(int(empty_after))),
                    ("write_pointer_before", str(write_pointer_before)),
                    ("write_pointer_after", str(write_pointer_after)),
                    ("read_pointer_before", str(read_pointer_before)),
                    ("read_pointer_after", str(read_pointer_after)),
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
        "sync.fifo",
        inspection.trace,
        f"sha256:{trace_sha256}",
        index.timescale_fs,
        anchor,
        source_anchors,
        (
            ("depth", str(selected_depth)),
            ("edge_count", str(len(observations))),
            ("hierarchy", hierarchy),
        ),
        tuple(observations),
        tuple(findings),
    )


def _source_anchors(root: Path, rtl_path: Path | None) -> tuple[SourceAnchor, ...]:
    if rtl_path is None:
        return ()
    resolved_root = root.resolve(strict=True)
    candidate = rtl_path if rtl_path.is_absolute() else resolved_root / rtl_path
    try:
        lexical = candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("FIFO RTL source is outside the repository") from error
    if ".." in lexical.parts:
        raise ValueError("FIFO RTL source path is invalid")
    current = resolved_root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("FIFO RTL source path contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("FIFO RTL source is missing") from error
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise ValueError("FIFO RTL source is outside the repository")
    content = resolved.read_bytes()
    if not content or len(content) > 1024 * 1024:
        raise ValueError("FIFO RTL source size is invalid")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("FIFO RTL source is not UTF-8") from error
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    snippets = (
        "assign read_accepted",
        "assign wr_ready",
        "assign write_accepted",
        "always_ff @(posedge clk)",
        "unique case",
    )
    selected_lines = tuple(
        index
        for index, line in enumerate(lines, start=1)
        if any(snippet in line for snippet in snippets)
    )
    if not selected_lines:
        raise ValueError("FIFO RTL source lacks debug anchor points")
    relative = resolved.relative_to(resolved_root).as_posix()
    return tuple(SourceAnchor(relative, line, line, digest) for line in selected_lines)


def _fifo_signals(hierarchy: str) -> tuple[str, ...]:
    if not hierarchy or any(value.isspace() for value in hierarchy):
        raise ValueError("FIFO hierarchy must be non-empty and contain no whitespace")
    return tuple(
        f"{hierarchy}.{name}"
        for name in (
            "clk",
            "rst_n",
            "wr_valid",
            "wr_ready",
            "write_accepted",
            "wr_data",
            "rd_valid",
            "rd_ready",
            "read_accepted",
            "rd_data",
            "level",
            "full",
            "empty",
            "write_pointer",
            "read_pointer",
        )
    )


def _depth(index: VcdIndex, hierarchy: str, supplied: int | None) -> int:
    if supplied is not None:
        if isinstance(supplied, bool) or supplied < 2:
            raise ValueError("FIFO depth must be at least two")
        return supplied
    value = index.value_at(f"{hierarchy}.DEPTH", 0)
    selected = _integer(value, f"{hierarchy}.DEPTH")
    if selected < 2:
        raise ValueError("FIFO depth must be at least two")
    return selected


def _bit_before(index: VcdIndex, signal: str, timestamp_fs: int) -> bool:
    return bool(_integer(index.value_before(signal, timestamp_fs), signal))


def _bit_at(index: VcdIndex, signal: str, timestamp_fs: int) -> bool:
    return bool(_integer(index.value_at(signal, timestamp_fs), signal))


def _integer_before(index: VcdIndex, signal: str, timestamp_fs: int) -> int:
    return _integer(index.value_before(signal, timestamp_fs), signal)


def _integer_at(index: VcdIndex, signal: str, timestamp_fs: int) -> int:
    return _integer(index.value_at(signal, timestamp_fs), signal)


def _integer(value: str | None, signal: str) -> int:
    if value is None or not value or any(bit not in "01" for bit in value):
        raise ValueError(f"FIFO debug signal is unknown at a sampled edge: {signal}")
    return int(value, 2)


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _event_summary(
    rst_n: bool,
    write_accepted: bool,
    read_accepted: bool,
    write_blocked: bool,
    read_blocked: bool,
    level_before: int,
    level_after: int,
) -> tuple[str, str, tuple[str, ...]]:
    if not rst_n:
        return (
            "reset-edge",
            f"Synchronous reset forced level {level_before} -> {level_after}.",
            ("fifo.reset",),
        )
    if write_accepted and read_accepted:
        return (
            "simultaneous-transfer",
            f"Simultaneous read/write preserved level {level_before} -> {level_after}.",
            ("fifo.simultaneous", "fifo.order"),
        )
    if write_accepted:
        return (
            "write-transfer",
            f"Write accepted; level changed {level_before} -> {level_after}.",
            ("fifo.write",),
        )
    if read_accepted:
        return (
            "read-transfer",
            f"Read accepted; level changed {level_before} -> {level_after}.",
            ("fifo.read", "fifo.order"),
        )
    if write_blocked:
        return (
            "write-blocked",
            f"Write request was backpressured at level {level_before}.",
            ("fifo.backpressure",),
        )
    if read_blocked:
        return (
            "read-blocked",
            f"Read request was blocked at level {level_before}.",
            ("fifo.backpressure",),
        )
    return (
        "idle-edge",
        f"No transfer; level remained {level_before} -> {level_after}.",
        ("fifo.status",),
    )
