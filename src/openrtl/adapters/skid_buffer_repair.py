"""Skid-buffer-specific repair strategy over generic debug contracts."""

from __future__ import annotations

from collections import defaultdict

from openrtl.adapters.waveforms import WaveformFocus
from openrtl.application import (
    DebugFinding,
    DebugSessionReport,
    RepairChange,
    RepairProposal,
    build_repair_proposal,
)
from openrtl.domain import ArtifactKind


_STRATEGIES: dict[str, tuple[str, str]] = {
    "reset": (
        "Restore synchronous skid-state reset.",
        "Active-low reset must clear retained occupancy on the sampled edge.",
    ),
    "refill-ready": (
        "Restore full-buffer same-edge refill readiness.",
        "A retained word leaving on this edge frees capacity for one replacement word.",
    ),
    "output-valid": (
        "Restore retained-or-pass-through output validity.",
        "Output validity must cover both retained and incoming words.",
    ),
    "input-handshake": (
        "Align internal input acceptance with valid/ready.",
        "The internal event must equal the interface handshake.",
    ),
    "output-handshake": (
        "Align internal output acceptance with valid/ready.",
        "The internal event must equal the interface handshake.",
    ),
    "retained-data": (
        "Restore retained-word output selection.",
        "While occupied, the output must expose the retained word before new input.",
    ),
    "occupancy": (
        "Correct occupancy updates for accepted transfers.",
        "The one-entry state must follow the push/pop combination at each edge.",
    ),
}


def propose_skid_buffer_repairs(
    report: DebugSessionReport,
    *,
    report_uri: str,
) -> RepairProposal:
    """Create a non-applying proposal covering every skid-buffer finding."""

    grouped: dict[str, list[DebugFinding]] = defaultdict(list)
    order: list[str] = []
    for finding in report.findings:
        category = _category(report, finding)
        if category not in grouped:
            order.append(category)
        grouped[category].append(finding)
    changes: list[RepairChange] = []
    all_known = True
    for category in order:
        selected = tuple(grouped[category])
        strategy = _STRATEGIES.get(category)
        if strategy is None:
            all_known = False
            summary = f"Investigate and correct the skid-buffer {category} invariant."
            rationale = "The linked edge contains a repeatable contract violation."
        else:
            summary, rationale = strategy
        changes.append(
            RepairChange(
                f"repair.change.skid.{category}",
                ArtifactKind.RTL,
                summary,
                rationale,
                tuple(value.finding_id for value in selected),
                tuple(dict.fromkeys(value.requirement_id for value in selected)),
                report.source_anchors,
                tuple(dict.fromkeys(value.waveform_anchor for value in selected)),
            )
        )
    return build_repair_proposal(
        report,
        report_uri=report_uri,
        changes=tuple(changes),
        confidence_percent=95 if all_known else 65,
        validation_steps=(
            "Review the skid-buffer source and waveform anchors before editing RTL.",
            "Re-run the same refill stimulus and require every linked finding to disappear.",
            "Run the randomized skid-buffer model and Verilator/cocotb checks.",
            "Run python tools/validate.py.",
        ),
    )


def skid_buffer_repair_focus(report: DebugSessionReport) -> WaveformFocus:
    """Select causal ready/valid signals around all skid-buffer findings."""

    if not report.findings:
        raise ValueError("skid-buffer repair focus requires debug findings")
    anchors = tuple(value.waveform_anchor for value in report.findings)
    intervals = tuple(
        later.timestamp_fs - earlier.timestamp_fs
        for earlier, later in zip(report.observations, report.observations[1:])
        if later.timestamp_fs > earlier.timestamp_fs
    )
    padding = min(intervals) // 2 if intervals else 0
    signals = tuple(
        signal
        for signal in report.waveform_anchor.signals
        if signal.rpartition(".")[2]
        in {
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
            "input_accepted",
            "output_accepted",
        }
    )
    return WaveformFocus(
        report.trace_uri,
        max(report.waveform_anchor.start_fs, min(value.start_fs for value in anchors) - padding),
        min(report.waveform_anchor.end_fs, max(value.end_fs for value in anchors) + padding),
        signals,
        tuple(sorted({marker for value in anchors for marker in value.markers_fs})),
    )


def _category(report: DebugSessionReport, finding: DebugFinding) -> str:
    prefix = f"{report.session_id}."
    if not finding.finding_id.startswith(prefix):
        raise ValueError("skid-buffer finding identity is outside its debug session")
    suffix = finding.finding_id[len(prefix) :]
    category, separator, timestamp = suffix.rpartition(".")
    if not separator or not category or not timestamp.isdigit():
        raise ValueError("skid-buffer finding identity lacks category and timestamp")
    return category
