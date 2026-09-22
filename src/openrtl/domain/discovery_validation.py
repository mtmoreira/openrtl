"""Fixed, non-narrative discovery validation diagnostics."""

from __future__ import annotations


DISCOVERY_VALIDATION_CATEGORIES = {
    **dict.fromkeys((
        "readiness_schema_invalid", "readiness_categories_missing", "readiness_category_invalid",
        "readiness_status_invalid", "readiness_anchor_invalid", "readiness_requirement_anchor_missing",
        "readiness_port_decisions_missing", "readiness_acceptance_decisions_missing",
    ), "readiness"),
    **dict.fromkeys((
        "expert_clarification_question_duplicate", "expert_clarification_question_unknown",
        "expert_clarification_repeated", "expert_clarification_question_limit",
    ), "questions"),
    **dict.fromkeys((
        "requirements_missing", "requirement_ids_duplicate", "port_direction_invalid",
        "port_width_invalid", "port_names_duplicate", "decision_ids_duplicate",
        "specification_too_large", "identifier_invalid", "stable_id_invalid",
    ), "specification"),
    **dict.fromkeys((
        "engineering_memory_id_duplicate", "engineering_memory_kind_invalid",
        "engineering_memory_provenance_invalid", "engineering_memory_too_large",
        "expert_cannot_confirm_user_memory", "raw_prompt_in_engineering_memory",
        "raw_reply_in_engineering_memory",
    ), "memory"),
    **dict.fromkeys((
        "object_fields_invalid", "list_invalid", "text_type_invalid", "text_size_invalid",
        "text_control_character_invalid", "expert_discussion_fields_invalid",
        "expert_discussion_reply_missing",
    ), "shape"),
}


class DiscoveryValidationError(ValueError):
    """Public error stays generic; only an allowlisted rule crosses the boundary."""

    def __init__(self, code: str | None) -> None:
        super().__init__("expert_output_invalid")
        self.validation_code = code if code in DISCOVERY_VALIDATION_CATEGORIES else None
        self.category_code = ("expert_output_" + DISCOVERY_VALIDATION_CATEGORIES[code] + "_invalid"
                              if self.validation_code else "expert_output_invalid")
