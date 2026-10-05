"""Closed, non-narrative diagnostics for generated artifact validation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


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
    "unresolved_signoff_findings", "python_syntax_invalid", "python_compile_resource_limit",
})


class DuplicateContributionPathError(ValueError):
    """Validated file-index pairs, with no rejected paths or artifact content."""

    def __init__(self, duplicate_locations: list[tuple[int, int]]) -> None:
        if (type(duplicate_locations) is not list or not 1 <= len(duplicate_locations) <= 63 or
                any(type(pair) is not tuple or len(pair) != 2 or
                    type(pair[0]) is not int or type(pair[1]) is not int or
                    not 0 <= pair[0] < pair[1] < 64 for pair in duplicate_locations) or
                len(set(duplicate_locations)) != len(duplicate_locations)):
            raise ValueError("duplicate_contribution_diagnostic_invalid")
        super().__init__("contribution_ownership_invalid")
        self.duplicate_locations = tuple(duplicate_locations)


def document_validation_feedback(error: object) -> list[dict[str, str | int]]:
    if type(error) is not DuplicateContributionPathError:
        return []
    return [{"field": f"files[{duplicate}].path", "duplicate_of": f"files[{first}].path",
             "expected": "Return a fresh complete proposal with exactly one unambiguous file entry "
                         "per path. Resolve competing versions explicitly while preserving all "
                         "required content and reviewed requirements; do not omit required artifacts."}
            for first, duplicate in error.duplicate_locations]


# Only errors reached after file ownership and contribution checks may request
# a new DV proposal. These trusted locations/rules contain no rejected values.
_MANIFEST_RULES = {
    "top": ({"identifier_invalid", "top_differs_from_specification"},
            "Copy manifest_contract.rtl_top_module_name exactly."),
    "sources": ({"source_list_invalid", "source_unavailable", "unlisted_rtl_source"},
                "List every manifest_contract.rtl_sources path exactly once."),
    "test_modules": ({"identifier_invalid", "test_modules_invalid", "test_module_unavailable"},
                     "Select existing flat dv/<Python_identifier>.py test files, without duplicates."),
    "expected_tests": ({"identifier_invalid", "expected_tests_invalid"},
                       "List every expected cocotb test function by its bare Python identifier, without duplicates."),
    "requirement_tests": ({"test_link_invalid", "requirement_test_links_incomplete"},
                          "Include exactly one row per manifest_contract.required_requirement_ids entry; "
                          "each tests list must be nonempty and refer only to expected_tests. "
                          "Assumption and decision IDs are not requirement IDs."),
    "seed": ({"seed_invalid"}, "Use a JSON integer from 0 through 2147483647 inclusive."),
}


class ManifestValidationError(ValueError):
    """A closed local manifest failure eligible for bounded DV correction."""

    def __init__(self, field: str, code: str) -> None:
        if field not in _MANIFEST_RULES or code not in _MANIFEST_RULES[field][0]:
            raise ValueError("manifest_diagnostic_invalid")
        super().__init__(code)
        self.field = "manifest." + field
        self.expected = _MANIFEST_RULES[field][1]


@contextmanager
def manifest_validation(field: str) -> Iterator[None]:
    """Annotate only fixed recoverable rules; never format unknown exceptions."""
    try:
        yield
    except ValueError as error:
        code = artifact_validation_code(error)
        if type(error) is ValueError and code in _MANIFEST_RULES[field][0]:
            assert code is not None
            raise ManifestValidationError(field, code) from None
        raise


def manifest_validation_feedback(error: object) -> dict[str, str] | None:
    if type(error) is not ManifestValidationError:
        return None
    return {"field": error.field, "expected": error.expected}


class DVValidationError(ValueError):
    """Collected local diagnostics, never Python exception text or source."""

    def __init__(self, manifest_error: ManifestValidationError | None,
                 syntax_locations: list[tuple[int, int, int]]) -> None:
        if (manifest_error is not None and type(manifest_error) is not ManifestValidationError or
                not syntax_locations or len(syntax_locations) > 64 or
                any(type(index) is not int or not 0 <= index < 64 or
                    type(line) is not int or line < 1 or type(column) is not int or column < 1
                    for index, line, column in syntax_locations)):
            raise ValueError("dv_diagnostic_invalid")
        code = "python_syntax_invalid" if manifest_error is None else manifest_error.args[0]
        super().__init__(code)
        self.manifest_error = manifest_error
        self.syntax_locations = tuple(syntax_locations)


def dv_validation_feedback(error: object) -> list[dict[str, str | int]]:
    feedback: list[dict[str, str | int]] = []
    manifest = error.manifest_error if type(error) is DVValidationError else error
    selected = manifest_validation_feedback(manifest)
    if selected is not None:
        feedback.append(dict(selected))
    if type(error) is DVValidationError:
        for index, line, column in error.syntax_locations:
            feedback.append({"field": f"files[{index}].content", "line": line, "column": column,
                             "expected": "Return syntactically valid Python. Compound statements such as "
                                         "for, if and while must start on a new line with an indented body. "
                                         "Preserve the approved test intent; do not remove checks."})
    return feedback


def artifact_validation_code(error: object) -> str | None:
    """Extract only a fixed local rule, without formatting arbitrary exceptions."""
    if not isinstance(error, ValueError):
        return None
    if type(error) not in (ValueError, ManifestValidationError, DVValidationError,
                          DuplicateContributionPathError) or len(error.args) != 1:
        return None
    code = error.args[0]
    return code if type(code) is str and code in ARTIFACT_VALIDATION_CODES else None
