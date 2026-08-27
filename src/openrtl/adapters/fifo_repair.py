"""FIFO-specific repair strategies over generic debug-session contracts."""

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
from openrtl.domain import ArtifactKind, SourceAnchor


_STRATEGIES: dict[str, tuple[str, str, str]] = {
    "write-handshake": (
        "Align internal write acceptance with valid/ready.",
        "The captured internal acceptance differs from the interface handshake at the linked edge.",
        "combinational",
    ),
    "read-handshake": (
        "Align internal read acceptance with valid/ready.",
        "The captured internal acceptance differs from the interface handshake at the linked edge.",
        "combinational",
    ),
    "backpressure": (
        "Correct full/read-through write backpressure.",
        "Write readiness must admit a write when capacity exists or a same-edge read frees capacity.",
        "combinational",
    ),
    "read-valid": (
        "Derive read validity from the empty state.",
        "The captured read-valid signal contradicts the pre-edge empty state.",
        "combinational",
    ),
    "level": (
        "Correct the accepted-transfer level update.",
        "The post-edge level must add accepted writes, subtract accepted reads, and remain stable for simultaneous transfers.",
        "sequential",
    ),
    "full": (
        "Correct the full flag comparison.",
        "The full flag must equal the post-edge level-to-depth comparison without truncation.",
        "combinational",
    ),
    "empty": (
        "Correct the empty flag comparison.",
        "The empty flag must assert exactly when the post-edge level is zero.",
        "combinational",
    ),
    "write-pointer": (
        "Correct write-pointer enable and wraparound.",
        "The write pointer must advance exactly once per accepted write and wrap at the configured last entry.",
        "sequential",
    ),
    "read-pointer": (
        "Correct read-pointer enable and wraparound.",
        "The read pointer must advance exactly once per accepted read and wrap at the configured last entry.",
        "sequential",
    ),
    "underflow": (
        "Prevent reads without a retained FIFO word.",
        "Read acceptance must be blocked while the modeled FIFO queue is empty.",
        "sequential",
    ),
    "ordering": (
        "Restore oldest-word read ordering.",
        "The read datapath and pointer selection must expose the oldest retained word.",
        "sequential",
    ),
}


def propose_fifo_repairs(
    report: DebugSessionReport,
    *,
    report_uri: str,
) -> RepairProposal:
    """Create a non-applying proposal that covers every FIFO debug finding."""

    grouped: dict[str, list[DebugFinding]] = defaultdict(list)
    category_order: list[str] = []
    for finding in report.findings:
        category = _finding_category(report, finding)
        if category not in grouped:
            category_order.append(category)
        grouped[category].append(finding)

    changes: list[RepairChange] = []
    all_known = True
    for category in category_order:
        selected = tuple(grouped[category])
        strategy = _STRATEGIES.get(category)
        if strategy is None:
            all_known = False
            summary = f"Investigate and correct the {category} invariant."
            rationale = "The debug session captured a repeatable requirement violation at the linked edge."
            anchor_group = "all"
        else:
            summary, rationale, anchor_group = strategy
        changes.append(
            RepairChange(
                f"repair.change.{category}",
                ArtifactKind.RTL,
                summary,
                rationale,
                tuple(value.finding_id for value in selected),
                tuple(dict.fromkeys(value.requirement_id for value in selected)),
                _source_anchors(report, anchor_group),
                tuple(dict.fromkeys(value.waveform_anchor for value in selected)),
            )
        )

    return build_repair_proposal(
        report,
        report_uri=report_uri,
        changes=tuple(changes),
        confidence_percent=90 if all_known else 60,
        validation_steps=(
            "Review the proposed source and waveform anchors before editing RTL.",
            "Re-run the focused failing stimulus and require every linked finding to disappear.",
            "Run python tools/validate.py.",
            "Run python tools/validate.py --with-verilator with the explicit local toolchain.",
        ),
    )


def fifo_repair_focus(report: DebugSessionReport) -> WaveformFocus:
    """Select the minimum waveform window and signals covering all findings."""

    if not report.findings:
        raise ValueError("repair focus requires debug findings")
    anchors = tuple(value.waveform_anchor for value in report.findings)
    signals = tuple(
        dict.fromkeys(signal for anchor in anchors for signal in anchor.signals)
    )
    return WaveformFocus(
        report.trace_uri,
        min(value.start_fs for value in anchors),
        max(value.end_fs for value in anchors),
        signals,
        tuple(sorted({marker for value in anchors for marker in value.markers_fs})),
    )


def _finding_category(report: DebugSessionReport, finding: DebugFinding) -> str:
    prefix = f"{report.session_id}."
    if not finding.finding_id.startswith(prefix):
        raise ValueError("FIFO finding identity is not scoped to its debug session")
    suffix = finding.finding_id[len(prefix) :]
    category, separator, timestamp = suffix.rpartition(".")
    if not separator or not category or not timestamp.isdigit():
        raise ValueError("FIFO finding identity lacks its category and timestamp")
    return category


def _source_anchors(
    report: DebugSessionReport,
    anchor_group: str,
) -> tuple[SourceAnchor, ...]:
    anchors = report.source_anchors
    if not anchors:
        raise ValueError("FIFO repair proposal requires RTL source anchors")
    if anchor_group == "combinational" and len(anchors) >= 3:
        return anchors[:3]
    if anchor_group == "sequential" and len(anchors) >= 2:
        return anchors[-2:]
    return anchors
