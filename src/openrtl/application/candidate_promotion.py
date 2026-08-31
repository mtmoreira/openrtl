"""Non-applying plans for promoting validated repair candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.domain._validation import digest, identifier, relative_path, unique_identifiers


def candidate_promotion_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class CandidatePromotionPlan:
    """Exact evidence and source bindings required before production promotion."""

    promotion_plan_id: str
    qualified_application_id: str
    qualified_application_digest: str
    application_id: str
    application_digest: str
    qualification_id: str
    qualification_digest: str
    proposal_id: str
    edit_plan_digest: str
    change_ids: tuple[str, ...]
    candidate_path: str
    candidate_digest: str
    target_path: str
    target_digest: str
    comparison_path: str
    comparison_digest: str
    evidence_path: str
    evidence_digest: str
    before_results_path: str
    before_results_digest: str
    before_waveform_path: str
    before_waveform_digest: str
    repaired_results_path: str
    repaired_results_digest: str
    repaired_waveform_path: str
    repaired_waveform_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "promotion_plan_id",
            "qualified_application_id",
            "application_id",
            "qualification_id",
            "proposal_id",
        ):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in (
            "qualified_application_digest",
            "application_digest",
            "qualification_digest",
            "edit_plan_digest",
            "candidate_digest",
            "target_digest",
            "comparison_digest",
            "evidence_digest",
            "before_results_digest",
            "before_waveform_digest",
            "repaired_results_digest",
            "repaired_waveform_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                digest(getattr(self, field_name), field_name),
            )
        for field_name in (
            "candidate_path",
            "target_path",
            "comparison_path",
            "evidence_path",
            "before_results_path",
            "before_waveform_path",
            "repaired_results_path",
            "repaired_waveform_path",
        ):
            object.__setattr__(
                self,
                field_name,
                relative_path(getattr(self, field_name), field_name),
            )
        changes = unique_identifiers(self.change_ids, "change_id")
        if not changes:
            raise ValueError("candidate promotion plan requires changes")
        object.__setattr__(self, "change_ids", changes)
        if self.candidate_digest == self.target_digest:
            raise ValueError("candidate promotion requires changed source bytes")
        if self.candidate_path == self.target_path:
            raise ValueError("candidate promotion planning requires a separate candidate")

    def _content(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "candidate": {
                "content_digest": self.candidate_digest,
                "path": self.candidate_path,
            },
            "lineage": {
                "application": {
                    "application_id": self.application_id,
                    "content_digest": self.application_digest,
                },
                "change_ids": list(self.change_ids),
                "edit_plan_digest": self.edit_plan_digest,
                "proposal_id": self.proposal_id,
                "qualification": {
                    "content_digest": self.qualification_digest,
                    "qualification_id": self.qualification_id,
                },
                "qualified_application": {
                    "content_digest": self.qualified_application_digest,
                    "qualified_application_id": self.qualified_application_id,
                },
            },
            "next_gate": {
                "exact_plan_digest_required": True,
                "explicit_production_promotion_required": True,
                "human_signoff_required": True,
                "operation": "repair promote-qualified-provider-candidate",
            },
            "promotion_plan_id": self.promotion_plan_id,
            "review": {
                "required_bindings": [
                    "promotion_plan_id",
                    "promotion_plan_digest",
                    "target_path",
                    "target_digest",
                    "candidate_digest",
                    "signoff_note",
                ],
                "signoff_role": "independent_signoff_reviewer",
            },
            "schema": "openrtl.candidate-promotion-plan.v1",
            "status": "awaiting_promotion_approval",
            "target": {
                "content_digest": self.target_digest,
                "path": self.target_path,
            },
            "validation": {
                "before_results": {
                    "content_digest": self.before_results_digest,
                    "path": self.before_results_path,
                },
                "before_waveform": {
                    "content_digest": self.before_waveform_digest,
                    "path": self.before_waveform_path,
                },
                "comparison": {
                    "content_digest": self.comparison_digest,
                    "path": self.comparison_path,
                    "status": "validated",
                },
                "evidence": {
                    "content_digest": self.evidence_digest,
                    "path": self.evidence_path,
                    "status": "passed",
                },
                "repaired_results": {
                    "content_digest": self.repaired_results_digest,
                    "path": self.repaired_results_path,
                },
                "repaired_waveform": {
                    "content_digest": self.repaired_waveform_digest,
                    "path": self.repaired_waveform_path,
                },
                "visibly_distinct": True,
            },
        }

    @property
    def content_digest(self) -> str:
        return candidate_promotion_digest(self._content())

    def payload(self) -> dict[str, Any]:
        return {**self._content(), "content_digest": self.content_digest}


def build_candidate_promotion_plan(
    *,
    qualified_application_id: str,
    qualified_application_digest: str,
    application_id: str,
    application_digest: str,
    qualification_id: str,
    qualification_digest: str,
    proposal_id: str,
    edit_plan_digest: str,
    change_ids: tuple[str, ...],
    candidate_path: str,
    candidate_digest: str,
    target_path: str,
    target_digest: str,
    comparison_path: str,
    comparison_digest: str,
    evidence_path: str,
    evidence_digest: str,
    before_results_path: str,
    before_results_digest: str,
    before_waveform_path: str,
    before_waveform_digest: str,
    repaired_results_path: str,
    repaired_results_digest: str,
    repaired_waveform_path: str,
    repaired_waveform_digest: str,
) -> CandidatePromotionPlan:
    seed = {
        "application_digest": application_digest,
        "before_results_digest": before_results_digest,
        "before_waveform_digest": before_waveform_digest,
        "candidate_digest": candidate_digest,
        "comparison_digest": comparison_digest,
        "evidence_digest": evidence_digest,
        "qualification_digest": qualification_digest,
        "qualified_application_digest": qualified_application_digest,
        "target_digest": target_digest,
    }
    token = hashlib.sha256(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return CandidatePromotionPlan(
        f"repair.promotion-plan.{token}",
        qualified_application_id,
        qualified_application_digest,
        application_id,
        application_digest,
        qualification_id,
        qualification_digest,
        proposal_id,
        edit_plan_digest,
        change_ids,
        candidate_path,
        candidate_digest,
        target_path,
        target_digest,
        comparison_path,
        comparison_digest,
        evidence_path,
        evidence_digest,
        before_results_path,
        before_results_digest,
        before_waveform_path,
        before_waveform_digest,
        repaired_results_path,
        repaired_results_digest,
        repaired_waveform_path,
        repaired_waveform_digest,
    )
