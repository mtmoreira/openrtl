from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from openrtl.adapters import LocalDesignCatalog
from openrtl.application import build_requirement_coverage, load_evaluation_cases
from openrtl.cli import main, validate_fifo_canary
from openrtl.domain import (
    ArtifactRef,
    EvidenceRecord,
    InterfacePort,
    PackageFile,
    PortDirection,
    RequirementAnchor,
    TrustLevel,
    DesignPackage,
)


class CliReviewCatalogTest(unittest.TestCase):
    def test_fifo_canary_and_cli_plan_are_provider_free(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_fifo_canary(root), ())
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(("plan", "--mode", "learn"))
        self.assertEqual(status, 0)
        self.assertIn("diagnosis", json.loads(output.getvalue()))

    def test_requirement_coverage_requires_artifact_and_evidence(self) -> None:
        evidence = EvidenceRecord(
            "ev.fifo.order",
            "FIFO ordering passed",
            (RequirementAnchor("fifo.order"),),
            (ArtifactRef("fifo.dv", 1),),
        )
        rows = build_requirement_coverage(("fifo.order", "fifo.reset"), (evidence,))
        self.assertEqual([row.requirement_id for row in rows], ["fifo.order", "fifo.reset"])
        self.assertTrue(rows[0].covered)
        self.assertFalse(rows[1].covered)

    def test_catalog_stores_verified_manifest_and_rejects_traversal(self) -> None:
        package = DesignPackage(
            "community.sync.fifo",
            "1.0.0",
            "sync.fifo",
            "Apache-2.0",
            TrustLevel.SIMULATION_VERIFIED,
            (InterfacePort("clk", PortDirection.INPUT, 1),),
            (),
            (PackageFile("rtl/sync_fifo.sv", "rtl", "sha256:" + "a" * 64),),
            ("ev.fifo.order",),
        )
        with tempfile.TemporaryDirectory() as directory:
            catalog = LocalDesignCatalog(Path(directory).resolve())
            destination = catalog.store_manifest(package)
            self.assertTrue(destination.is_file())
            self.assertEqual(catalog.package_ids(), ("community.sync.fifo",))
            self.assertEqual(catalog.versions(package.package_id), ("1.0.0",))
            self.assertEqual(
                catalog.read_manifest(package.package_id, package.version)["content_digest"],
                package.content_digest,
            )
            with self.assertRaises(ValueError):
                catalog.read_manifest("../outside", "1.0.0")

    def test_v1_evaluation_dataset_covers_build_diagnosis_escalation_and_learning(self) -> None:
        root = Path(__file__).resolve().parents[1]
        cases = load_evaluation_cases(root / "evals/openrtl_v1.json")
        self.assertEqual(len(cases), 4)
        self.assertTrue(any(value.must_escalate for value in cases))
        self.assertTrue(any("waveform_focus" in value.required_outputs for value in cases))


if __name__ == "__main__":
    unittest.main()
