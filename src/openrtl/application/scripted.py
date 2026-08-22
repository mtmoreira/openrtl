"""Provider-free end-to-end FIFO workflow used as executable architecture."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from openrtl.application.reviews import RequirementCoverage, build_requirement_coverage
from openrtl.domain import (
    ArtifactKind,
    ArtifactRef,
    ArtifactRevision,
    DesignPackage,
    EvidenceRecord,
    ExperienceLevel,
    InteractionMode,
    InterfacePort,
    LearnerProfile,
    LearningSession,
    LogAnchor,
    PackageFile,
    Parameter,
    PortDirection,
    ProjectKnowledgeBase,
    RequirementAnchor,
    RunBundle,
    RunStatus,
    SourceAnchor,
    TeachingStep,
    TrustLevel,
    WaveformAnchor,
)


FIFO_REQUIREMENTS = (
    "fifo.reset",
    "fifo.write",
    "fifo.read",
    "fifo.order",
    "fifo.backpressure",
    "fifo.simultaneous",
    "fifo.wrap",
    "fifo.status",
)


@dataclass(frozen=True)
class ScriptedFifoResult:
    knowledge: ProjectKnowledgeBase
    package: DesignPackage
    coverage: tuple[RequirementCoverage, ...]
    learning: LearningSession | None


def run_scripted_fifo(root: Path, mode: InteractionMode) -> ScriptedFifoResult:
    root = root.resolve()
    if root == Path("/"):
        raise ValueError("scripted workflow root must be bounded")
    knowledge = ProjectKnowledgeBase()
    sources = (
        ("fifo.spec", ArtifactKind.SPECIFICATION, "examples/fifo/spec.md"),
        ("fifo.model", ArtifactKind.REFERENCE_MODEL, "examples/fifo/model.py"),
        ("fifo.rtl", ArtifactKind.RTL, "examples/fifo/rtl/sync_fifo.sv"),
        ("fifo.dv", ArtifactKind.DV, "examples/fifo/dv/test_sync_fifo.py"),
    )
    refs: list[ArtifactRef] = []
    digests: dict[str, str] = {}
    for artifact_id, kind, relative in sources:
        content_digest = _digest(root / relative)
        digests[relative] = content_digest
        ref = ArtifactRef(artifact_id, 1)
        knowledge.artifacts.add(
            ArtifactRevision(
                ref,
                kind,
                relative,
                content_digest,
                f"Verified FIFO {kind.value} collateral",
                FIFO_REQUIREMENTS,
            )
        )
        refs.append(ref)
    run_ref = ArtifactRef("fifo.run", 1)
    knowledge.artifacts.add(
        ArtifactRevision(
            run_ref,
            ArtifactKind.RUN,
            "runs/fifo.scripted/run.json",
            _text_digest("scripted-pass"),
            "Provider-free scripted FIFO pass",
            FIFO_REQUIREMENTS,
            tuple(refs),
        )
    )
    evidence = EvidenceRecord(
        "ev.fifo.scripted",
        "All FIFO requirements are linked to model, RTL, DV, log, and waveform evidence.",
        (
            *(RequirementAnchor(value) for value in FIFO_REQUIREMENTS),
            SourceAnchor(
                "examples/fifo/rtl/sync_fifo.sv",
                1,
                len((root / "examples/fifo/rtl/sync_fifo.sv").read_text().splitlines()),
                digests["examples/fifo/rtl/sync_fifo.sv"],
            ),
            LogAnchor("fifo.scripted", "regression.passed"),
            WaveformAnchor(
                "fifo.scripted.trace",
                10_000_000,
                40_000_000,
                ("sync_fifo.wr_valid", "sync_fifo.rd_valid", "sync_fifo.level"),
                (20_000_000, 30_000_000),
            ),
        ),
        (*refs, run_ref),
    )
    knowledge.evidence.add(evidence)
    knowledge.add_run(
        RunBundle(
            "fifo.scripted",
            RunStatus.PASSED,
            "verilator.cocotb",
            17,
            (*refs, run_ref),
            (evidence.evidence_id,),
            "runs/fifo.scripted/events.jsonl",
            "runs/fifo.scripted/waves.vcd",
        )
    )
    package = DesignPackage(
        "community.sync.fifo",
        "1.0.0",
        "sync.fifo",
        "Apache-2.0",
        TrustLevel.SIMULATION_VERIFIED,
        (
            InterfacePort("clk", PortDirection.INPUT, 1),
            InterfacePort("rst_n", PortDirection.INPUT, 1),
            InterfacePort("wr_valid", PortDirection.INPUT, 1),
            InterfacePort("wr_ready", PortDirection.OUTPUT, 1),
            InterfacePort("wr_data", PortDirection.INPUT, 8),
            InterfacePort("rd_valid", PortDirection.OUTPUT, 1),
            InterfacePort("rd_ready", PortDirection.INPUT, 1),
            InterfacePort("rd_data", PortDirection.OUTPUT, 8),
        ),
        (Parameter("width", 8, 1, 1024), Parameter("depth", 4, 2, 65536)),
        tuple(
            PackageFile(relative, kind.value, digests[relative])
            for _, kind, relative in sources
        ),
        (evidence.evidence_id,),
    )
    coverage = build_requirement_coverage(FIFO_REQUIREMENTS, (evidence,))
    learning = _learning(evidence) if mode is InteractionMode.LEARN else None
    return ScriptedFifoResult(knowledge, package, coverage, learning)


def _learning(evidence: EvidenceRecord) -> LearningSession:
    profile = LearnerProfile("fifo.learner", ExperienceLevel.BEGINNER, ("Understand ready-valid flow",))
    session = LearningSession("fifo.learning", profile)
    session.add_step(
        TeachingStep(
            "fifo.simultaneous.transfer",
            "Explain simultaneous read and write.",
            "A full FIFO can accept a write when the same edge also removes its oldest word.",
            "Inspect the handshake, level, and pointer logic beside the focused trace interval.",
            "Why does level remain constant on a simultaneous accepted transfer?",
            tuple(anchor for anchor in evidence.anchors if isinstance(anchor, (SourceAnchor, WaveformAnchor))),
        )
    )
    return session


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _text_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
