"""Block-neutral contracts for hash-bound simulation package candidates."""

from __future__ import annotations

from dataclasses import dataclass

from openrtl.application.reviews import RequirementCoverage
from openrtl.domain import (
    ArtifactKind,
    ArtifactRef,
    DesignPackage,
    InterfacePort,
    Parameter,
    VerifiedRunEvidence,
)


@dataclass(frozen=True)
class SimulationProfileFile:
    artifact_ref: ArtifactRef
    kind: ArtifactKind
    path: str


@dataclass(frozen=True)
class VerifiedSimulationProfile:
    profile_id: str
    profile_uri: str
    profile_digest: str
    manifest_schema: str
    design_id: str
    package_id: str
    package_version: str
    license_id: str
    run_id: str
    tool_profile_id: str
    testcase: str
    top: str
    seed: int
    requirements: tuple[str, ...]
    files: tuple[SimulationProfileFile, ...]
    run_ref: ArtifactRef
    source_record_key: str
    log_artifact_key: str
    results_artifact_key: str
    waveform_artifact_key: str
    focus_signals: tuple[str, ...]
    evidence_id: str
    ports: tuple[InterfacePort, ...]
    parameters: tuple[Parameter, ...]

    @property
    def artifact_refs(self) -> tuple[ArtifactRef, ...]:
        return tuple(value.artifact_ref for value in self.files) + (self.run_ref,)


@dataclass(frozen=True)
class VerifiedPackageCandidate:
    profile: VerifiedSimulationProfile
    verified_run: VerifiedRunEvidence
    package: DesignPackage
    coverage: tuple[RequirementCoverage, ...]
    catalog_manifest: str | None = None
