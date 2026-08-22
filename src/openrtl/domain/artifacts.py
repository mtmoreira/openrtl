"""Versioned engineering artifacts and dependency lineage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from openrtl.domain._validation import digest, identifier, nonempty, relative_path


class ArtifactKind(str, Enum):
    REQUIREMENTS = "requirements"
    SPECIFICATION = "specification"
    MICROARCHITECTURE = "microarchitecture"
    IMPLEMENTATION_PLAN = "implementation_plan"
    VERIFICATION_PLAN = "verification_plan"
    TEST_PLAN = "test_plan"
    INTEGRATION_PLAN = "integration_plan"
    REFERENCE_MODEL = "reference_model"
    RTL = "rtl"
    ASSERTIONS = "assertions"
    DV = "dv"
    RUN = "run"
    DIAGNOSIS = "diagnosis"
    REVIEW = "review"
    DESIGN_PACKAGE = "design_package"


@dataclass(frozen=True, order=True)
class ArtifactRef:
    artifact_id: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", identifier(self.artifact_id, "artifact_id"))
        if self.revision < 1:
            raise ValueError("revision must be positive")

    @property
    def key(self) -> str:
        return f"{self.artifact_id}@{self.revision}"


@dataclass(frozen=True)
class ArtifactRevision:
    ref: ArtifactRef
    kind: ArtifactKind
    uri: str
    content_digest: str
    summary: str
    requirement_ids: tuple[str, ...] = ()
    dependencies: tuple[ArtifactRef, ...] = ()
    supersedes: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "uri", relative_path(self.uri, "uri"))
        object.__setattr__(self, "content_digest", digest(self.content_digest, "content_digest"))
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))
        requirements = tuple(identifier(value, "requirement_id") for value in self.requirement_ids)
        dependencies = tuple(self.dependencies)
        if len(set(requirements)) != len(requirements):
            raise ValueError("requirement_ids must be unique")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("dependencies must be unique")
        if self.ref in dependencies:
            raise ValueError("an artifact cannot depend on itself")
        if self.supersedes is not None:
            if self.supersedes.artifact_id != self.ref.artifact_id:
                raise ValueError("supersedes must reference the same artifact_id")
            if self.supersedes.revision >= self.ref.revision:
                raise ValueError("supersedes must reference an earlier revision")
        object.__setattr__(self, "requirement_ids", requirements)
        object.__setattr__(self, "dependencies", dependencies)


class ArtifactGraph:
    """Mutable project aggregate with immutable artifact revisions."""

    def __init__(self) -> None:
        self._revisions: dict[ArtifactRef, ArtifactRevision] = {}
        self._latest: dict[str, ArtifactRef] = {}
        self._dependents: dict[ArtifactRef, set[ArtifactRef]] = {}

    def add(self, revision: ArtifactRevision) -> None:
        if revision.ref in self._revisions:
            raise ValueError(f"artifact revision already exists: {revision.ref.key}")
        current = self._latest.get(revision.ref.artifact_id)
        if current is None:
            if revision.ref.revision != 1 or revision.supersedes is not None:
                raise ValueError("the first artifact revision must be revision 1")
        else:
            if revision.ref.revision != current.revision + 1:
                raise ValueError("artifact revisions must be contiguous")
            if revision.supersedes != current:
                raise ValueError("a new revision must supersede the current latest revision")
        for dependency in revision.dependencies:
            if dependency not in self._revisions:
                raise ValueError(f"unknown artifact dependency: {dependency.key}")
        self._revisions[revision.ref] = revision
        self._latest[revision.ref.artifact_id] = revision.ref
        for dependency in revision.dependencies:
            self._dependents.setdefault(dependency, set()).add(revision.ref)

    def resolve(self, ref: ArtifactRef) -> ArtifactRevision:
        try:
            return self._revisions[ref]
        except KeyError as error:
            raise KeyError(f"unknown artifact revision: {ref.key}") from error

    def latest(self, artifact_id: str) -> ArtifactRevision:
        normalized = identifier(artifact_id, "artifact_id")
        try:
            return self.resolve(self._latest[normalized])
        except KeyError as error:
            raise KeyError(f"unknown artifact: {normalized}") from error

    def latest_by_kind(self, kinds: Iterable[ArtifactKind]) -> tuple[ArtifactRevision, ...]:
        selected = set(kinds)
        return tuple(
            sorted(
                (
                    self.resolve(ref)
                    for ref in self._latest.values()
                    if self.resolve(ref).kind in selected
                ),
                key=lambda revision: revision.ref,
            )
        )

    def affected_by(self, changed: ArtifactRef) -> tuple[ArtifactRef, ...]:
        self.resolve(changed)
        affected: set[ArtifactRef] = set()
        pending = list(self._dependents.get(changed, set()))
        while pending:
            candidate = pending.pop()
            if candidate in affected:
                continue
            affected.add(candidate)
            pending.extend(self._dependents.get(candidate, set()))
        return tuple(sorted(affected))

    def all_revisions(self) -> tuple[ArtifactRevision, ...]:
        return tuple(self._revisions[ref] for ref in sorted(self._revisions))
