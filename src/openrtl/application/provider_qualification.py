"""Canonical evidence for deterministic provider-output qualification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.domain._validation import (
    digest,
    identifier,
    relative_path,
    unique_identifiers,
)


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def provider_qualification_digest(payload: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


@dataclass(frozen=True)
class ProviderOutputQualificationReport:
    """A non-applying receipt binding provider lineage to one typed edit plan."""

    qualification_id: str
    provider_plan_id: str
    provider_plan_digest: str
    provider_execution_digest: str
    request_id: str
    request_digest: str
    invocation_id: str
    invocation_report_digest: str
    suggestion_id: str
    suggestion_report_digest: str
    edit_spec_digest: str
    edit_spec_file_digest: str
    proposal_id: str
    debug_session_id: str
    source_path: str
    source_digest: str
    edit_plan_id: str
    edit_plan_digest: str
    planning_id: str
    planning_report_digest: str
    change_ids: tuple[str, ...]
    edit_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "qualification_id",
            "provider_plan_id",
            "request_id",
            "invocation_id",
            "suggestion_id",
            "proposal_id",
            "debug_session_id",
            "edit_plan_id",
            "planning_id",
        ):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in (
            "provider_plan_digest",
            "provider_execution_digest",
            "request_digest",
            "invocation_report_digest",
            "suggestion_report_digest",
            "edit_spec_digest",
            "edit_spec_file_digest",
            "source_digest",
            "edit_plan_digest",
            "planning_report_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                digest(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_path",
            relative_path(self.source_path, "source_path"),
        )
        changes = unique_identifiers(self.change_ids, "change_id")
        edits = unique_identifiers(self.edit_ids, "edit_id")
        if not changes or not edits:
            raise ValueError("provider output qualification requires changes and edits")
        object.__setattr__(self, "change_ids", changes)
        object.__setattr__(self, "edit_ids", edits)

    def _content(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "debug_session_id": self.debug_session_id,
            "edit_plan": {
                "content_digest": self.edit_plan_digest,
                "edit_plan_id": self.edit_plan_id,
            },
            "edit_spec": {
                "canonical_digest": self.edit_spec_digest,
                "file_digest": self.edit_spec_file_digest,
            },
            "lineage": {
                "change_ids": list(self.change_ids),
                "edit_ids": list(self.edit_ids),
                "invocation": {
                    "content_digest": self.invocation_report_digest,
                    "invocation_id": self.invocation_id,
                },
                "provider_execution_digest": self.provider_execution_digest,
                "provider_plan": {
                    "content_digest": self.provider_plan_digest,
                    "plan_id": self.provider_plan_id,
                },
                "request": {
                    "content_digest": self.request_digest,
                    "request_id": self.request_id,
                },
                "suggestion": {
                    "content_digest": self.suggestion_report_digest,
                    "suggestion_id": self.suggestion_id,
                },
            },
            "next_gate": {
                "exact_edit_plan_digest_required": True,
                "human_review_required": True,
                "operation": "repair apply-source-edits",
            },
            "planning": {
                "content_digest": self.planning_report_digest,
                "planning_id": self.planning_id,
            },
            "proposal_id": self.proposal_id,
            "provider_output_trusted": False,
            "qualification_id": self.qualification_id,
            "schema": "openrtl.provider-output-qualification.v1",
            "source": {
                "content_digest": self.source_digest,
                "path": self.source_path,
            },
            "status": "awaiting_review",
        }

    @property
    def content_digest(self) -> str:
        return provider_qualification_digest(self._content())

    def payload(self) -> dict[str, Any]:
        return {**self._content(), "content_digest": self.content_digest}


def build_provider_output_qualification_report(
    *,
    provider_plan_id: str,
    provider_plan_digest: str,
    provider_execution_digest: str,
    request_id: str,
    request_digest: str,
    invocation_id: str,
    invocation_report_digest: str,
    suggestion_id: str,
    suggestion_report_digest: str,
    edit_spec_digest: str,
    edit_spec_file_digest: str,
    proposal_id: str,
    debug_session_id: str,
    source_path: str,
    source_digest: str,
    edit_plan_id: str,
    edit_plan_digest: str,
    planning_id: str,
    planning_report_digest: str,
    change_ids: tuple[str, ...],
    edit_ids: tuple[str, ...],
) -> ProviderOutputQualificationReport:
    seed = {
        "edit_plan_digest": edit_plan_digest,
        "invocation_report_digest": invocation_report_digest,
        "planning_report_digest": planning_report_digest,
        "provider_execution_digest": provider_execution_digest,
        "provider_plan_digest": provider_plan_digest,
        "suggestion_report_digest": suggestion_report_digest,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return ProviderOutputQualificationReport(
        f"repair.provider-qualification.{token}",
        provider_plan_id,
        provider_plan_digest,
        provider_execution_digest,
        request_id,
        request_digest,
        invocation_id,
        invocation_report_digest,
        suggestion_id,
        suggestion_report_digest,
        edit_spec_digest,
        edit_spec_file_digest,
        proposal_id,
        debug_session_id,
        source_path,
        source_digest,
        edit_plan_id,
        edit_plan_digest,
        planning_id,
        planning_report_digest,
        change_ids,
        edit_ids,
    )
