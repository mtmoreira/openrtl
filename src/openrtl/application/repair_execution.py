"""Review decisions and immutable reports for bounded repair application."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.domain._validation import (
    digest,
    identifier,
    nonempty,
    relative_path,
    unique_identifiers,
)


@dataclass(frozen=True)
class RepairApproval:
    proposal_id: str
    approved_change_ids: tuple[str, ...]
    edit_plan_digest: str
    review_note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", identifier(self.proposal_id, "proposal_id"))
        changes = unique_identifiers(self.approved_change_ids, "approved_change_id")
        if not changes:
            raise ValueError("repair approval requires at least one change")
        object.__setattr__(self, "approved_change_ids", changes)
        object.__setattr__(
            self,
            "edit_plan_digest",
            digest(self.edit_plan_digest, "edit_plan_digest"),
        )
        object.__setattr__(self, "review_note", nonempty(self.review_note, "review_note"))


@dataclass(frozen=True)
class SourceEdit:
    edit_id: str
    change_id: str
    operation: str
    start_byte: int
    end_byte: int
    expected_before: str
    expected_before_digest: str
    replacement: str
    replacement_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "edit_id", identifier(self.edit_id, "edit_id"))
        object.__setattr__(self, "change_id", identifier(self.change_id, "change_id"))
        if self.operation != "replace_exact_bytes":
            raise ValueError("source edit operation is not allowlisted")
        if (
            isinstance(self.start_byte, bool)
            or isinstance(self.end_byte, bool)
            or self.start_byte < 0
            or self.end_byte <= self.start_byte
        ):
            raise ValueError("source edit byte range is invalid")
        before = self.expected_before.encode("utf-8")
        replacement = self.replacement.encode("utf-8")
        if not before or before == replacement:
            raise ValueError("source edit must replace non-empty bytes with different bytes")
        if self.end_byte - self.start_byte != len(before):
            raise ValueError("source edit byte range does not match expected bytes")
        object.__setattr__(
            self,
            "expected_before_digest",
            digest(self.expected_before_digest, "expected_before_digest"),
        )
        object.__setattr__(
            self,
            "replacement_digest",
            digest(self.replacement_digest, "replacement_digest"),
        )
        if self.expected_before_digest != _content_digest(before):
            raise ValueError("source edit expected bytes digest is invalid")
        if self.replacement_digest != _content_digest(replacement):
            raise ValueError("source edit replacement digest is invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "edit_id": self.edit_id,
            "end_byte": self.end_byte,
            "expected_before": self.expected_before,
            "expected_before_digest": self.expected_before_digest,
            "operation": self.operation,
            "replacement": self.replacement,
            "replacement_digest": self.replacement_digest,
            "start_byte": self.start_byte,
        }


@dataclass(frozen=True)
class SourceEditPlan:
    edit_plan_id: str
    proposal_id: str
    debug_session_id: str
    source_path: str
    source_digest: str
    edits: tuple[SourceEdit, ...]

    def __post_init__(self) -> None:
        for field_name in ("edit_plan_id", "proposal_id", "debug_session_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "source_path", relative_path(self.source_path, "source_path"))
        object.__setattr__(
            self,
            "source_digest",
            digest(self.source_digest, "source_digest"),
        )
        edits = tuple(self.edits)
        if not edits or len({value.edit_id for value in edits}) != len(edits):
            raise ValueError("source edit plan requires uniquely identified edits")
        ordered = tuple(sorted(edits, key=lambda value: value.start_byte))
        if any(left.end_byte > right.start_byte for left, right in zip(ordered, ordered[1:])):
            raise ValueError("source edit byte ranges must not overlap")
        object.__setattr__(self, "edits", ordered)

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "debug_session_id": self.debug_session_id,
            "edit_plan_id": self.edit_plan_id,
            "edits": [value.payload() for value in self.edits],
            "proposal_id": self.proposal_id,
            "schema": "openrtl.source-edit-plan.v1",
            "source": {
                "content_digest": self.source_digest,
                "path": self.source_path,
            },
            "status": "proposed",
        }

    @property
    def content_digest(self) -> str:
        return canonical_payload_digest(self.payload())


def build_source_edit_plan(
    *,
    proposal_id: str,
    debug_session_id: str,
    source_path: str,
    source: bytes,
    edit_specs: tuple[dict[str, str], ...],
) -> SourceEditPlan:
    """Build a typed plan from untrusted, reviewable exact-replacement specs."""

    edits: list[SourceEdit] = []
    for spec in edit_specs:
        expected = spec.get("expected_before")
        replacement = spec.get("replacement")
        if not isinstance(expected, str) or not isinstance(replacement, str):
            raise ValueError("source edit specification requires text bytes")
        expected_bytes = expected.encode("utf-8")
        if source.count(expected_bytes) != 1:
            raise ValueError("source edit expected bytes must occur exactly once")
        start = source.index(expected_bytes)
        edits.append(
            SourceEdit(
                spec.get("edit_id", ""),
                spec.get("change_id", ""),
                spec.get("operation", ""),
                start,
                start + len(expected_bytes),
                expected,
                _content_digest(expected_bytes),
                replacement,
                _content_digest(replacement.encode("utf-8")),
            )
        )
    seed = {
        "debug_session_id": debug_session_id,
        "edits": [value.payload() for value in edits],
        "proposal_id": proposal_id,
        "source_digest": _content_digest(source),
        "source_path": source_path,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return SourceEditPlan(
        f"repair.edit-plan.{token}",
        proposal_id,
        debug_session_id,
        source_path,
        _content_digest(source),
        tuple(edits),
    )


def canonical_payload_digest(payload: dict[str, Any]) -> str:
    return _content_digest(_canonical_json(payload))


@dataclass(frozen=True)
class RepairApplicationReport:
    application_id: str
    proposal_id: str
    debug_session_id: str
    edit_plan_id: str
    edit_plan_digest: str
    change_ids: tuple[str, ...]
    edit_ids: tuple[str, ...]
    source_path: str
    output_path: str
    source_digest_before: str
    source_digest_after: str
    changed_line_numbers: tuple[int, ...]
    review_note: str

    def __post_init__(self) -> None:
        for field_name in (
            "application_id",
            "proposal_id",
            "debug_session_id",
            "edit_plan_id",
        ):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        changes = unique_identifiers(self.change_ids, "change_id")
        edits = unique_identifiers(self.edit_ids, "edit_id")
        lines = tuple(self.changed_line_numbers)
        if not changes or not edits:
            raise ValueError("repair application requires changes and edits")
        if not lines or len(set(lines)) != len(lines) or any(value < 1 for value in lines):
            raise ValueError("repair application requires unique positive changed lines")
        object.__setattr__(self, "change_ids", changes)
        object.__setattr__(self, "edit_ids", edits)
        object.__setattr__(self, "changed_line_numbers", lines)
        object.__setattr__(
            self,
            "edit_plan_digest",
            digest(self.edit_plan_digest, "edit_plan_digest"),
        )
        object.__setattr__(
            self,
            "source_digest_before",
            digest(self.source_digest_before, "source_digest_before"),
        )
        object.__setattr__(
            self,
            "source_digest_after",
            digest(self.source_digest_after, "source_digest_after"),
        )
        if self.source_digest_before == self.source_digest_after:
            raise ValueError("repair application must change the source digest")
        object.__setattr__(self, "source_path", relative_path(self.source_path, "source_path"))
        object.__setattr__(self, "output_path", relative_path(self.output_path, "output_path"))
        object.__setattr__(self, "review_note", nonempty(self.review_note, "review_note"))

    def payload(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "authorization": {
                "approved_change_ids": self.change_ids,
                "approved_edit_plan_digest": self.edit_plan_digest,
                "proposal_id": self.proposal_id,
                "review_note": self.review_note,
            },
            "changed_line_numbers": self.changed_line_numbers,
            "debug_session_id": self.debug_session_id,
            "edit_ids": self.edit_ids,
            "edit_plan_digest": self.edit_plan_digest,
            "edit_plan_id": self.edit_plan_id,
            "output_path": self.output_path,
            "proposal_id": self.proposal_id,
            "schema": "openrtl.repair-application.v2",
            "source_digest_after": self.source_digest_after,
            "source_digest_before": self.source_digest_before,
            "source_path": self.source_path,
            "status": "applied_to_candidate",
        }


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _content_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
