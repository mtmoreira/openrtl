"""Typed, non-applying contracts for expert-proposed source edits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.domain import ContextPack, ExpertRole
from openrtl.domain._validation import digest, identifier, relative_path, unique_identifiers


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _payload_digest(payload: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def context_pack_payload(context: ContextPack) -> dict[str, Any]:
    return {
        "attempt": context.attempt,
        "items": [
            {
                "content_digest": item.content_digest,
                "item_id": item.item_id,
                "item_type": item.item_type,
                "summary": item.summary,
                "uri": item.uri,
            }
            for item in context.items
        ],
        "objective": context.objective,
        "pack_id": context.pack_id,
        "role": context.role.value,
    }


@dataclass(frozen=True)
class ExpertSourceEditRequest:
    request_id: str
    context_pack: ContextPack
    proposal_id: str
    proposal_digest: str
    debug_session_id: str
    debug_session_digest: str
    source_path: str
    source_digest: str
    change_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("request_id", "proposal_id", "debug_session_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in ("proposal_digest", "debug_session_digest", "source_digest"):
            object.__setattr__(self, field_name, digest(getattr(self, field_name), field_name))
        object.__setattr__(self, "source_path", relative_path(self.source_path, "source_path"))
        changes = unique_identifiers(self.change_ids, "change_id")
        if not changes:
            raise ValueError("expert source edit request requires changes")
        object.__setattr__(self, "change_ids", changes)
        if self.context_pack.role is not ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER:
            raise ValueError("source edit suggestions require the diagnosis closure expert")

    @property
    def context_pack_digest(self) -> str:
        return _payload_digest(context_pack_payload(self.context_pack))

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "change_ids": list(self.change_ids),
            "context_pack": {
                "content_digest": self.context_pack_digest,
                "payload": context_pack_payload(self.context_pack),
            },
            "debug_session": {
                "content_digest": self.debug_session_digest,
                "session_id": self.debug_session_id,
            },
            "expert_role": ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER.value,
            "output_contract": {
                "allowed_operations": ["replace_exact_bytes"],
                "schema": "openrtl.expert-source-edit-output.v1",
            },
            "proposal": {
                "content_digest": self.proposal_digest,
                "proposal_id": self.proposal_id,
            },
            "request_id": self.request_id,
            "schema": "openrtl.expert-source-edit-request.v1",
            "source": {
                "content_digest": self.source_digest,
                "path": self.source_path,
            },
            "status": "awaiting_expert_output",
        }

    @property
    def content_digest(self) -> str:
        return _payload_digest(self.payload())


def build_expert_source_edit_request(
    *,
    context_pack: ContextPack,
    proposal_id: str,
    proposal_digest: str,
    debug_session_id: str,
    debug_session_digest: str,
    source_path: str,
    source_digest: str,
    change_ids: tuple[str, ...],
) -> ExpertSourceEditRequest:
    seed = {
        "change_ids": change_ids,
        "context_pack_digest": _payload_digest(context_pack_payload(context_pack)),
        "debug_session_digest": debug_session_digest,
        "proposal_digest": proposal_digest,
        "source_digest": source_digest,
        "source_path": source_path,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return ExpertSourceEditRequest(
        f"repair.expert-request.{token}",
        context_pack,
        proposal_id,
        proposal_digest,
        debug_session_id,
        debug_session_digest,
        source_path,
        source_digest,
        change_ids,
    )


@dataclass(frozen=True)
class ExpertSourceEditReport:
    suggestion_id: str
    request_id: str
    request_digest: str
    context_pack_id: str
    context_pack_digest: str
    proposal_id: str
    debug_session_id: str
    source_path: str
    source_digest: str
    response_digest: str
    edit_spec_digest: str
    change_ids: tuple[str, ...]
    edit_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "suggestion_id",
            "request_id",
            "context_pack_id",
            "proposal_id",
            "debug_session_id",
        ):
            object.__setattr__(self, field_name, identifier(getattr(self, field_name), field_name))
        for field_name in (
            "request_digest",
            "context_pack_digest",
            "source_digest",
            "response_digest",
            "edit_spec_digest",
        ):
            object.__setattr__(self, field_name, digest(getattr(self, field_name), field_name))
        object.__setattr__(self, "source_path", relative_path(self.source_path, "source_path"))
        changes = unique_identifiers(self.change_ids, "change_id")
        edits = unique_identifiers(self.edit_ids, "edit_id")
        if not changes or not edits:
            raise ValueError("expert source edit report requires changes and edits")
        object.__setattr__(self, "change_ids", changes)
        object.__setattr__(self, "edit_ids", edits)

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "change_ids": list(self.change_ids),
            "context_pack": {
                "content_digest": self.context_pack_digest,
                "pack_id": self.context_pack_id,
            },
            "debug_session_id": self.debug_session_id,
            "edit_ids": list(self.edit_ids),
            "edit_spec_digest": self.edit_spec_digest,
            "expert_role": ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER.value,
            "next_gate": {
                "operation": "repair draft-source-edits",
                "qualification_required": True,
            },
            "proposal_id": self.proposal_id,
            "request": {
                "content_digest": self.request_digest,
                "request_id": self.request_id,
            },
            "response_digest": self.response_digest,
            "schema": "openrtl.expert-source-edit-report.v1",
            "source": {
                "content_digest": self.source_digest,
                "path": self.source_path,
            },
            "status": "awaiting_qualification",
            "suggestion_id": self.suggestion_id,
            "trusted": False,
        }


def build_expert_source_edit_report(
    request: ExpertSourceEditRequest,
    *,
    response_digest: str,
    edit_spec_digest: str,
    change_ids: tuple[str, ...],
    edit_ids: tuple[str, ...],
) -> ExpertSourceEditReport:
    seed = {
        "edit_spec_digest": edit_spec_digest,
        "request_digest": request.content_digest,
        "response_digest": response_digest,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return ExpertSourceEditReport(
        f"repair.expert-suggestion.{token}",
        request.request_id,
        request.content_digest,
        request.context_pack.pack_id,
        request.context_pack_digest,
        request.proposal_id,
        request.debug_session_id,
        request.source_path,
        request.source_digest,
        response_digest,
        edit_spec_digest,
        change_ids,
        edit_ids,
    )
