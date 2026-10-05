"""Non-executing Python validation and bounded combined DV diagnostics."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch
import warnings

from openrtl.domain.artifact_validation import (
    DVValidationError, ManifestValidationError, artifact_validation_code, dv_validation_feedback,
)
from openrtl.domain.dv_validation import validate_dv_files


def python_file(content: str, *, name: str = "test_wire") -> dict[str, str]:
    return {"path": "dv/" + name + ".py", "content": content}


class DvValidationTest(unittest.TestCase):
    def test_python_is_compiled_without_importing_or_executing_generated_code(self) -> None:
        source = ("import synthetic_dependency_that_does_not_exist\n"
                  "raise AssertionError('Generated module must never execute during validation')\n")
        self.assertIsNone(validate_dv_files([python_file(source)]))

    def test_captured_compound_statement_shape_reports_only_fixed_location_fields(self) -> None:
        source = ("async def transfer(dut):\n" + "    # Synthetic padding\n" * 198 +
                  "    dut.synthetic.value = 0; for _ in range(2): await synthetic_tick()\n")
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(DVValidationError) as caught:
                validate_dv_files([python_file(source)])

        error = caught.exception
        self.assertEqual(artifact_validation_code(error), "python_syntax_invalid")
        self.assertEqual(error.args, ("python_syntax_invalid",))
        feedback = dv_validation_feedback(error)
        self.assertEqual(len(feedback), 1)
        self.assertEqual(set(feedback[0]), {"field", "expected", "line", "column"})
        self.assertEqual(feedback[0]["field"], "files[0].content")
        self.assertEqual(feedback[0]["line"], 200)
        self.assertGreater(feedback[0]["column"], 0)
        self.assertTrue(feedback[0]["expected"])
        self.assertNotIn("dut.synthetic", str(error) + str(feedback))
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_semantically_invalid_control_statements_are_rejected_by_compile(self) -> None:
        for source in ("return 1\n", "break\n", "continue\n", "await trigger()\n"):
            with self.subTest(source=source), self.assertRaises(DVValidationError) as caught:
                validate_dv_files([python_file(source)])
            self.assertEqual(artifact_validation_code(caught.exception), "python_syntax_invalid")
            self.assertEqual(dv_validation_feedback(caught.exception)[0]["line"], 1)

    def test_manifest_and_all_file_syntax_failures_share_one_feedback_response(self) -> None:
        manifest_error = ManifestValidationError("requirement_tests", "requirement_test_links_incomplete")
        files = [python_file("def transfer():\n    pass\n"),
                 python_file("def broken(:\n", name="test_second"),
                 python_file("def also_broken(:\n", name="test_third")]

        with self.assertRaises(DVValidationError) as caught:
            validate_dv_files(files, manifest_error=manifest_error)

        self.assertEqual(artifact_validation_code(caught.exception), "requirement_test_links_incomplete")
        feedback = dv_validation_feedback(caught.exception)
        self.assertEqual([row["field"] for row in feedback],
                         ["manifest.requirement_tests", "files[1].content", "files[2].content"])
        self.assertEqual(feedback[0], {"field": manifest_error.field, "expected": manifest_error.expected})
        for row in feedback[1:]:
            self.assertEqual(row["line"], 1)
            self.assertGreater(row["column"], 0)
        self.assertNotIn("broken", str(caught.exception) + str(feedback))
        self.assertNotIn("test_second", str(feedback))

    def test_manifest_fix_does_not_accept_unfixed_python(self) -> None:
        source = "def transfer(:\n"
        prior = ManifestValidationError("requirement_tests", "requirement_test_links_incomplete")
        with self.assertRaises(DVValidationError) as combined:
            validate_dv_files([python_file(source)], manifest_error=prior)
        with self.assertRaises(DVValidationError) as syntax_only:
            validate_dv_files([python_file(source)])
        self.assertEqual(artifact_validation_code(combined.exception), "requirement_test_links_incomplete")
        self.assertEqual(artifact_validation_code(syntax_only.exception), "python_syntax_invalid")
        self.assertEqual(dv_validation_feedback(combined.exception)[1:],
                         dv_validation_feedback(syntax_only.exception))
        self.assertIsNone(validate_dv_files([python_file("def transfer():\n    return 1\n")]))

    def test_valid_python_cannot_mask_an_invalid_manifest(self) -> None:
        prior = ManifestValidationError("test_modules", "test_module_unavailable")
        with self.assertRaises(ValueError) as caught:
            validate_dv_files([python_file("def transfer():\n    return 1\n")], manifest_error=prior)
        self.assertEqual(artifact_validation_code(caught.exception), "test_module_unavailable")
        self.assertEqual(dv_validation_feedback(caught.exception),
                         [{"field": prior.field, "expected": prior.expected}])

    def test_compiler_warnings_cannot_print_generated_source(self) -> None:
        # CPython emits a SyntaxWarning for identity comparison with literals.
        source = "if 1 is 2:\n    pass\n"
        stdout, stderr = StringIO(), StringIO()
        with warnings.catch_warnings(record=True) as emitted, redirect_stdout(stdout), redirect_stderr(stderr):
            warnings.simplefilter("always")
            self.assertIsNone(validate_dv_files([python_file(source)]))
        self.assertEqual(emitted, [])
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_compiler_resource_errors_have_no_retry_authority_or_private_text(self) -> None:
        for kind in (RecursionError, MemoryError, OverflowError):
            with self.subTest(kind=kind.__name__):
                marker = "synthetic private compiler exception content"
                with patch("builtins.compile", side_effect=kind(marker)):
                    with self.assertRaises(ValueError) as caught:
                        validate_dv_files([python_file("value = 1\n")])
                error = caught.exception
                self.assertIs(type(error), ValueError)
                self.assertEqual(error.args, ("python_compile_resource_limit",))
                self.assertEqual(artifact_validation_code(error), "python_compile_resource_limit")
                self.assertEqual(dv_validation_feedback(error), [])
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)
                self.assertNotIn(marker, str(error))


if __name__ == "__main__":
    unittest.main()
