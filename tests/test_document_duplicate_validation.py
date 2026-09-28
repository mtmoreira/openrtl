"""Closed duplicate diagnostics without changing artifact acceptance rules."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from openrtl.domain.artifact_validation import (
    DuplicateContributionPathError, artifact_validation_code, document_validation_feedback,
)
from openrtl.domain.design_session import MAX_ARTIFACT_BYTES, MAX_CONTEXT_BYTES, validate_files


STAGE_PATHS = {
    "architecture": "docs/architecture.md",
    "verification_plan": "docs/verification-plan.md",
    "reference_model": "model/reference.py",
    "rtl": "rtl/block.sv",
    "assertions": "rtl/assertions.sv",
    "dv": "dv/test_block.py",
    "diagnosis": "rtl/block.sv",
}


class DuplicateContributionDiagnosticTest(unittest.TestCase):
    def test_feedback_contains_only_validated_locations_and_fixed_instruction(self) -> None:
        locations = [(0, 2), (1, 3), (0, 4)]
        error = DuplicateContributionPathError(locations)
        locations.append((0, 5))
        self.assertEqual(error.args, ("contribution_ownership_invalid",))
        self.assertEqual(error.duplicate_locations, ((0, 2), (1, 3), (0, 4)))
        self.assertEqual(artifact_validation_code(error), "contribution_ownership_invalid")
        feedback = document_validation_feedback(error)
        self.assertEqual([(row["field"], row["duplicate_of"]) for row in feedback],
                         [("files[2].path", "files[0].path"),
                          ("files[3].path", "files[1].path"),
                          ("files[4].path", "files[0].path")])
        self.assertTrue(all(set(row) == {"field", "duplicate_of", "expected"} for row in feedback))
        self.assertTrue(all(row["expected"] == feedback[0]["expected"] for row in feedback))
        self.assertIn("fresh complete proposal", str(feedback[0]["expected"]))
        self.assertIn("preserving all required content", str(feedback[0]["expected"]))
        feedback[0]["field"] = "SYNTHETIC_PRIVATE"
        self.assertEqual(document_validation_feedback(error)[0]["field"], "files[2].path")

    def test_maximum_diagnostic_size(self) -> None:
        error = DuplicateContributionPathError([(0, index) for index in range(1, 64)])
        self.assertEqual(len(document_validation_feedback(error)), 63)

    def test_malformed_diagnostic_inputs_are_rejected_without_formatting(self) -> None:
        class PrivateIndex(int):
            def __str__(self) -> str:
                raise AssertionError("Untrusted values must not be formatted")

        invalid: list[Any] = [
            None, "private", {}, (), [], [(0, 0)], [(1, 0)], [(-1, 1)], [(0, 64)],
            [(False, 1)], [(0, True)], [(0.0, 1)], [(0, "1")], [(PrivateIndex(0), 1)],
            [[0, 1]], [(0,)], [(0, 1, 2)], [None], [{"first": 0, "duplicate": 1}],
            [(0, 1), (0, 1)], [(0, index) for index in range(1, 65)],
        ]
        for index, value in enumerate(invalid):
            with self.subTest(case=index), self.assertRaisesRegex(
                    ValueError, "^duplicate_contribution_diagnostic_invalid$") as caught:
                DuplicateContributionPathError(value)
            self.assertIs(type(caught.exception), ValueError)

    def test_only_exact_typed_error_enables_feedback(self) -> None:
        class PrivateError(ValueError):
            def __str__(self) -> str:
                raise AssertionError("Unknown exceptions must not be formatted")

        class FakeDuplicate(DuplicateContributionPathError):
            pass

        errors = (None, object(), "contribution_ownership_invalid",
                  ValueError("contribution_ownership_invalid"), PrivateError("contribution_ownership_invalid"),
                  FakeDuplicate([(0, 1)]))
        for index, error in enumerate(errors):
            with self.subTest(case=index):
                self.assertEqual(document_validation_feedback(error), [])
        self.assertIsNone(artifact_validation_code(FakeDuplicate([(0, 1)])))


class DuplicateContributionValidationTest(unittest.TestCase):
    def test_same_and_competing_content_are_both_rejected_for_every_stage(self) -> None:
        for stage, path in STAGE_PATHS.items():
            for content in ("SYNTHETIC_FIRST", "SYNTHETIC_SECOND"):
                rows = [{"path": path, "content": "SYNTHETIC_FIRST"},
                        {"path": path, "content": content}]
                original = copy.deepcopy(rows)
                with self.subTest(stage=stage, competing=content != "SYNTHETIC_FIRST"), \
                        self.assertRaises(DuplicateContributionPathError) as caught:
                    validate_files(rows, stage)
                self.assertEqual(caught.exception.duplicate_locations, ((0, 1),))
                self.assertEqual(rows, original)
                self.assertNotIn(path, str(document_validation_feedback(caught.exception)))
                self.assertNotIn("SYNTHETIC", str(document_validation_feedback(caught.exception)))

    def test_repeated_paths_point_to_the_first_occurrence(self) -> None:
        paths = ("docs/one.md", "docs/two.md", "docs/one.md", "docs/two.md", "docs/one.md")
        rows = [{"path": path, "content": f"version {index}"} for index, path in enumerate(paths)]
        with self.assertRaises(DuplicateContributionPathError) as caught:
            validate_files(rows, "verification_plan")
        self.assertEqual(caught.exception.duplicate_locations, ((0, 2), (1, 3), (0, 4)))

    def test_all_64_rows_are_checked_and_reported(self) -> None:
        rows = [{"path": "docs/plan.md", "content": "plan"} for _ in range(64)]
        with self.assertRaises(DuplicateContributionPathError) as caught:
            validate_files(rows, "verification_plan")
        self.assertEqual(caught.exception.duplicate_locations,
                         tuple((0, index) for index in range(1, 64)))

    def test_valid_multifile_proposals_are_returned_unchanged(self) -> None:
        for stage, path in STAGE_PATHS.items():
            prefix, filename = path.split("/", 1)
            rows = [{"path": path, "content": "first\n"},
                    {"path": f"{prefix}/extra_{filename}", "content": "second\n"}]
            original = copy.deepcopy(rows)
            with self.subTest(stage=stage):
                result = validate_files(rows, stage)
                self.assertEqual(rows, original)
                self.assertEqual(result, original)
                self.assertIs(result[0], rows[0])
                self.assertIs(result[1], rows[1])

    def test_all_diagnosis_roots_remain_valid(self) -> None:
        rows = [{"path": path, "content": "content"}
                for path in ("rtl/block.sv", "dv/test_block.py", "model/reference.py")]
        self.assertEqual(validate_files(rows, "diagnosis"), rows)

    def test_later_wrong_root_overrides_duplicates_for_every_stage(self) -> None:
        for stage, path in STAGE_PATHS.items():
            wrong = "rtl/unowned.sv" if path.startswith("docs/") else "docs/unowned.md"
            rows = [{"path": path, "content": "first"}, {"path": path, "content": "second"},
                    {"path": wrong, "content": "third"}]
            with self.subTest(stage=stage), self.assertRaisesRegex(
                    ValueError, "^contribution_ownership_invalid$") as caught:
                validate_files(rows, stage)
            self.assertIs(type(caught.exception), ValueError)
            self.assertEqual(document_validation_feedback(caught.exception), [])

    def test_invalid_row_anywhere_overrides_duplicate_feedback(self) -> None:
        invalid_rows: list[tuple[dict[str, Any], str]] = [
            ({"path": "docs/../escape.md", "content": "text"}, "source_path_invalid"),
            ({"path": "docs/plan.py", "content": "text"}, "source_extension_invalid"),
            ({"path": "docs/extra.md", "content": None}, "text_type_invalid"),
            ({"path": "docs/extra.md", "content": ""}, "text_size_invalid"),
            ({"path": "docs/extra.md", "content": "bad\x00text"}, "text_control_character_invalid"),
            ({"path": "docs/extra.md", "content": "x" * (MAX_ARTIFACT_BYTES + 1)}, "text_size_invalid"),
            ({"path": "docs/extra.md", "content": "text", "extra": "private"}, "object_fields_invalid"),
        ]
        for row, code in invalid_rows:
            for index in range(3):
                rows = [{"path": "docs/plan.md", "content": "first"},
                        {"path": "docs/plan.md", "content": "second"}]
                rows.insert(index, row)
                original = copy.deepcopy(rows)
                with self.subTest(code=code, index=index), self.assertRaisesRegex(
                        ValueError, f"^{code}$") as caught:
                    validate_files(rows, "verification_plan")
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(document_validation_feedback(caught.exception), [])
                self.assertEqual(rows, original)

    def test_invalid_duplicate_content_overrides_duplicate_feedback(self) -> None:
        rows = [{"path": "docs/plan.md", "content": "first"},
                {"path": "docs/plan.md", "content": 12}]
        with self.assertRaisesRegex(ValueError, "^text_type_invalid$") as caught:
            validate_files(rows, "verification_plan")
        self.assertIs(type(caught.exception), ValueError)

    def test_aggregate_limit_counts_every_duplicate(self) -> None:
        self.assertEqual(MAX_CONTEXT_BYTES, 2 * MAX_ARTIFACT_BYTES)
        rows = [{"path": "docs/plan.md", "content": "x" * MAX_ARTIFACT_BYTES},
                {"path": "docs/plan.md", "content": "y" * MAX_ARTIFACT_BYTES},
                {"path": "docs/plan.md", "content": "z"}]
        with self.assertRaisesRegex(ValueError, "^contribution_too_large$") as caught:
            validate_files(rows, "verification_plan")
        self.assertIs(type(caught.exception), ValueError)
        self.assertEqual(document_validation_feedback(caught.exception), [])

    def test_file_count_is_not_reduced_by_deduplication(self) -> None:
        rows = [{"path": "docs/plan.md", "content": "plan"} for _ in range(65)]
        for value, code in ((rows, "list_invalid"), ([], "contribution_files_missing"),
                            (tuple(rows), "list_invalid")):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, f"^{code}$") as caught:
                validate_files(value, "verification_plan")
            self.assertIs(type(caught.exception), ValueError)


if __name__ == "__main__":
    unittest.main()
