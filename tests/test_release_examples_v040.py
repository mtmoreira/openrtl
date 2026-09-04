"""Fail-closed checks for installed 0.4 simulation acceptance."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tools.composed_package_matrix import MATRIX
from tools.validate_release import ReleaseManifest, ReleaseValidationError
from tools.validate_release_candidate import validate_candidate_metadata
from tools.verify_release_examples_v040 import validate_matrix_proof


class ReleaseExamplesV040Test(unittest.TestCase):
    def proof(self) -> dict[str, Any]:
        return {"schema": "openrtl.composed-package-matrix.v1", "status": "passed",
                "case_count": 3, "provider_called": False, "remote_operations": False,
                "cases": [{"case_id": value.case_id,
                           "configuration": {"width": value.width, "depth": value.depth,
                                             "capacity": value.depth + 1, "seed": value.seed},
                           "coverage": {"counts": {"max_occupancy": value.depth + 1}},
                           "producer_waveform": {"sha256": "a" * 64},
                           "consumer_waveform": {"sha256": "a" * 64}} for value in MATRIX]}

    def test_accepts_complete_installed_matrix(self) -> None:
        validate_matrix_proof(self.proof())

    def test_rejects_missing_case_waveform_drift_and_incomplete_coverage(self) -> None:
        original = self.proof()
        missing = copy.deepcopy(original)
        missing["cases"].pop()
        drift = copy.deepcopy(original)
        drift["cases"][0]["consumer_waveform"]["sha256"] = "b" * 64
        coverage = copy.deepcopy(original)
        coverage["cases"][2]["coverage"]["counts"]["max_occupancy"] = 0
        for payload in (missing, drift, coverage):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_matrix_proof(payload)

    def test_candidate_metadata_binds_040_to_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nversion="0.4.0"\ndependencies=["agentrig==0.3.0"]\n')
            validate_candidate_metadata(root, ReleaseManifest("0.4.0", "0" * 40, ()))
            with self.assertRaises(ReleaseValidationError):
                validate_candidate_metadata(root, ReleaseManifest("0.3.0", "0" * 40, ()))
