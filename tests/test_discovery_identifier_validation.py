"""Trusted identifier repair locations; rejected values never become diagnostics."""

from __future__ import annotations

import unittest

from openrtl.domain.design_session import (
    SpecificationIdentifierError, name, specification_name, validate_spec,
)
from openrtl.domain.discovery_validation import IDENTIFIER_EXPECTATION, identifier_validation_feedback
from tests.test_hardware_specification import parameterized_fifo_spec


class DiscoveryIdentifierValidationTest(unittest.TestCase):
    def test_all_specification_identifier_paths_retain_the_original_error_code(self) -> None:
        for location, expected in (
                ("top", "specification.top"),
                ("port", "specification.ports[1].name"),
                ("parameter", "specification.hardware_specification.parameters[1].name")):
            with self.subTest(location=location):
                spec = parameterized_fifo_spec()
                invalid = "openrtl.hardware-specification.v1"
                if location == "top":
                    spec["top"] = invalid
                elif location == "port":
                    spec["ports"][1]["name"] = invalid
                else:
                    spec["hardware_specification"]["parameters"][1]["name"] = invalid
                with self.assertRaises(SpecificationIdentifierError) as caught:
                    validate_spec(spec)
                self.assertEqual(str(caught.exception), "identifier_invalid")
                self.assertEqual(identifier_validation_feedback(caught.exception), {
                    "field": expected, "expected": IDENTIFIER_EXPECTATION})
                self.assertNotIn(invalid, repr(caught.exception.__dict__))

    def test_non_identifier_text_errors_keep_their_existing_types_and_codes(self) -> None:
        for value, code in ((None, "text_type_invalid"), (False, "text_type_invalid"),
                            (32, "text_type_invalid"), ("", "text_size_invalid"),
                            ("A" * 129, "text_size_invalid"),
                            ("bad\x01name", "text_control_character_invalid")):
            with self.subTest(code=code, value=value):
                with self.assertRaises(ValueError) as caught:
                    specification_name(value, "top")
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), code)
                self.assertIsNone(identifier_validation_feedback(caught.exception))
        with self.assertRaises(ValueError) as caught:
            name("bad.name")
        self.assertIs(type(caught.exception), ValueError)
        self.assertEqual(str(caught.exception), "identifier_invalid")
        self.assertIsNone(identifier_validation_feedback(caught.exception))

    def test_valid_identifiers_are_not_normalized(self) -> None:
        for value in ("fifo", "_valid_12", "A" * 128):
            self.assertEqual(specification_name(value, "top"), value)
        for value in ("bad.name", "bad name", "1bad", "bad-name", "é"):
            with self.assertRaisesRegex(SpecificationIdentifierError, "^identifier_invalid$"):
                specification_name(value, "top")

    def test_diagnostic_locations_are_closed_and_indices_bounded(self) -> None:
        for location, index in (("private.value", None), ("top", 0), ("port", None),
                                ("port", True), ("port", -1), ("port", 64),
                                ("parameter", "1"), ("parameter", 64)):
            with self.subTest(location=location, index=index):
                with self.assertRaisesRegex(ValueError, "^identifier_diagnostic_invalid$"):
                    SpecificationIdentifierError(location, index=index)
        for location in ("port", "parameter"):
            for index in (0, 63):
                error = SpecificationIdentifierError(location, index=index)
                self.assertIn(f"[{index}]", identifier_validation_feedback(error)["field"])

        class UntrustedSubclass(SpecificationIdentifierError):
            pass

        self.assertIsNone(identifier_validation_feedback(UntrustedSubclass("top")))


if __name__ == "__main__":
    unittest.main()
