"""Closed, non-narrative diagnostics for generated artifact validation."""

from __future__ import annotations


# Only rules from local contribution, manifest, and signoff validation belong
# here. Never add provider error text, file paths, or generated source content.
ARTIFACT_VALIDATION_CODES = frozenset({
    "object_fields_invalid", "list_invalid", "text_type_invalid", "text_size_invalid",
    "text_control_character_invalid", "identifier_invalid", "stable_id_invalid",
    "source_path_invalid", "source_extension_invalid", "contribution_files_missing",
    "contribution_ownership_invalid", "contribution_too_large", "artifact_owner_conflict",
    "project_artifacts_exceed_bound", "stage_cannot_replace_simulation_manifest",
    "repair_outside_existing_rtl", "repair_has_no_change", "repair_outside_reviewed_change_scope",
    "change_stage_paths_differ_from_review", "change_manifest_differs_from_review",
    "top_differs_from_specification", "source_list_invalid", "source_unavailable",
    "unlisted_rtl_source", "test_modules_invalid", "test_module_unavailable",
    "expected_tests_invalid", "test_link_invalid", "requirement_test_links_incomplete",
    "seed_invalid", "independent_model_tests_missing", "review_verdict_invalid",
    "unresolved_signoff_findings",
})


def artifact_validation_code(error: object) -> str | None:
    """Extract only a fixed local rule, without formatting arbitrary exceptions."""
    if type(error) is not ValueError or len(error.args) != 1:
        return None
    code = error.args[0]
    return code if type(code) is str and code in ARTIFACT_VALIDATION_CODES else None
