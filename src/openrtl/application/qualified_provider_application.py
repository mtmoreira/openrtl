"""Explicit approval and receipts for provider-qualified candidate repairs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.application.provider_qualification import ProviderOutputQualificationReport
from openrtl.application.repair_execution import RepairApplicationReport, RepairApproval
from openrtl.domain._validation import digest, identifier, nonempty, unique_identifiers


def qualified_provider_application_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class QualifiedProviderRepairApproval:
    """Human approval bound to one exact provider qualification and edit plan."""

    qualification_id: str
    qualification_digest: str
    proposal_id: str
    approved_change_ids: tuple[str, ...]
    edit_plan_digest: str
    review_note: str

    def __post_init__(self) -> None:
        for field_name in ("qualification_id", "proposal_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in ("qualification_digest", "edit_plan_digest"):
            object.__setattr__(
                self,
                field_name,
                digest(getattr(self, field_name), field_name),
            )
        changes = unique_identifiers(self.approved_change_ids, "approved_change_id")
        if not changes:
            raise ValueError("provider-qualified approval requires at least one change")
        object.__setattr__(self, "approved_change_ids", changes)
        note = nonempty(self.review_note, "review_note")
        if len(note) > 512:
            raise ValueError("provider-qualified approval review note exceeds its bound")
        object.__setattr__(self, "review_note", note)

    def payload(self) -> dict[str, Any]:
        return {
            "approved_change_ids": list(self.approved_change_ids),
            "edit_plan_digest": self.edit_plan_digest,
            "proposal_id": self.proposal_id,
            "qualification": {
                "content_digest": self.qualification_digest,
                "qualification_id": self.qualification_id,
            },
            "review_note": self.review_note,
            "schema": "openrtl.qualified-provider-repair-approval.v1",
        }

    @property
    def content_digest(self) -> str:
        return qualified_provider_application_digest(self.payload())

    def require_matches(self, qualification: ProviderOutputQualificationReport) -> None:
        if (
            self.qualification_id != qualification.qualification_id
            or self.qualification_digest != qualification.content_digest
            or self.proposal_id != qualification.proposal_id
            or self.approved_change_ids != qualification.change_ids
            or self.edit_plan_digest != qualification.edit_plan_digest
        ):
            raise ValueError("provider-qualified approval does not match exact qualification")

    def repair_approval(self) -> RepairApproval:
        return RepairApproval(
            self.proposal_id,
            self.approved_change_ids,
            self.edit_plan_digest,
            self.review_note,
        )


@dataclass(frozen=True)
class QualifiedProviderApplicationReport:
    """Receipt proving an exact provider qualification gated a candidate write."""

    qualified_application_id: str
    qualification_id: str
    qualification_digest: str
    approval_digest: str
    application: RepairApplicationReport

    def __post_init__(self) -> None:
        for field_name in ("qualified_application_id", "qualification_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in ("qualification_digest", "approval_digest"):
            object.__setattr__(
                self,
                field_name,
                digest(getattr(self, field_name), field_name),
            )

    def payload(self) -> dict[str, Any]:
        application_payload = self.application.payload()
        return {
            "application": {
                "application_id": self.application.application_id,
                "content_digest": qualified_provider_application_digest(
                    application_payload
                ),
                "output_path": self.application.output_path,
                "source_digest_after": self.application.source_digest_after,
                "source_digest_before": self.application.source_digest_before,
            },
            "authorization": {
                "approval_digest": self.approval_digest,
                "candidate_only": True,
                "human_review_required": True,
                "production_source_modified": False,
                "qualification_digest_matched": True,
            },
            "qualified_application_id": self.qualified_application_id,
            "qualification": {
                "content_digest": self.qualification_digest,
                "qualification_id": self.qualification_id,
            },
            "schema": "openrtl.qualified-provider-application.v1",
            "status": "applied_to_candidate",
        }


def build_qualified_provider_application_report(
    qualification: ProviderOutputQualificationReport,
    approval: QualifiedProviderRepairApproval,
    application: RepairApplicationReport,
) -> QualifiedProviderApplicationReport:
    approval.require_matches(qualification)
    if (
        application.proposal_id != qualification.proposal_id
        or application.debug_session_id != qualification.debug_session_id
        or application.edit_plan_id != qualification.edit_plan_id
        or application.edit_plan_digest != qualification.edit_plan_digest
        or application.change_ids != qualification.change_ids
        or application.edit_ids != qualification.edit_ids
        or application.source_path != qualification.source_path
    ):
        raise ValueError("candidate application differs from provider qualification")
    seed = {
        "application_id": application.application_id,
        "approval_digest": approval.content_digest,
        "qualification_digest": qualification.content_digest,
    }
    token = hashlib.sha256(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return QualifiedProviderApplicationReport(
        f"repair.qualified-application.{token}",
        qualification.qualification_id,
        qualification.content_digest,
        approval.content_digest,
        application,
    )
