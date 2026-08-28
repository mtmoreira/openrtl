"""Review decisions and immutable reports for bounded repair application."""

from __future__ import annotations

from dataclasses import dataclass
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
    review_note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", identifier(self.proposal_id, "proposal_id"))
        changes = unique_identifiers(self.approved_change_ids, "approved_change_id")
        if not changes:
            raise ValueError("repair approval requires at least one change")
        object.__setattr__(self, "approved_change_ids", changes)
        object.__setattr__(self, "review_note", nonempty(self.review_note, "review_note"))


@dataclass(frozen=True)
class RepairApplicationReport:
    application_id: str
    proposal_id: str
    debug_session_id: str
    change_ids: tuple[str, ...]
    source_path: str
    output_path: str
    source_digest_before: str
    source_digest_after: str
    changed_line_numbers: tuple[int, ...]
    review_note: str

    def __post_init__(self) -> None:
        for field_name in ("application_id", "proposal_id", "debug_session_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        changes = unique_identifiers(self.change_ids, "change_id")
        lines = tuple(self.changed_line_numbers)
        if not changes:
            raise ValueError("repair application requires at least one change")
        if not lines or len(set(lines)) != len(lines) or any(value < 1 for value in lines):
            raise ValueError("repair application requires unique positive changed lines")
        object.__setattr__(self, "change_ids", changes)
        object.__setattr__(self, "changed_line_numbers", lines)
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
                "proposal_id": self.proposal_id,
                "review_note": self.review_note,
            },
            "changed_line_numbers": self.changed_line_numbers,
            "debug_session_id": self.debug_session_id,
            "output_path": self.output_path,
            "proposal_id": self.proposal_id,
            "schema": "openrtl.repair-application.v1",
            "source_digest_after": self.source_digest_after,
            "source_digest_before": self.source_digest_before,
            "source_path": self.source_path,
            "status": "applied_to_candidate",
        }
