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
        "expert_question_memory_conflict", "expert_question_resolution_invalid",
        "expert_question_plan_invalid", "expert_clarification_late_topic",
        "expert_specification_refinement_missing",
    ), "questions"),
    **dict.fromkeys((
        "requirements_missing", "requirement_ids_duplicate", "port_direction_invalid",
        "port_width_invalid", "port_names_duplicate", "decision_ids_duplicate",
        "specification_too_large", "identifier_invalid", "stable_id_invalid",
        "hardware_specification_required", "hardware_specification_schema_invalid",
        "hardware_specification_sections_missing", "hardware_specification_section_invalid",
        "hardware_specification_section_status_invalid", "hardware_specification_section_order_invalid",
        "hardware_specification_purpose_missing", "hardware_specification_parameter_section_invalid",
        "parameter_type_invalid", "parameter_names_duplicate",
    ), "specification"),
    **dict.fromkeys((
        "engineering_memory_id_duplicate", "engineering_memory_kind_invalid",
        "engineering_memory_provenance_invalid", "engineering_memory_too_large",
        "expert_cannot_confirm_user_memory", "raw_prompt_in_engineering_memory",
        "raw_reply_in_engineering_memory",
        "expert_memory_confirmation_changed",
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
        selected = code if code is not None and code in DISCOVERY_VALIDATION_CATEGORIES else None
        self.validation_code = selected
        self.category_code = ("expert_output_" + DISCOVERY_VALIDATION_CATEGORIES[selected] + "_invalid"
                              if selected is not None else "expert_output_invalid")
