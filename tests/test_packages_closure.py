from __future__ import annotations

import unittest

from openrtl.domain.closure import (
    ClosureDecision,
    ClosurePolicy,
    ClosureTracker,
    RepairAttempt,
)
from openrtl.domain.design import InterfacePort, Parameter, PortDirection
from openrtl.domain.packages import (
    DesignPackage,
    InterfaceRequirement,
    PackageFile,
    TrustLevel,
    analyze_compatibility,
)


DIGEST = "sha256:" + "e" * 64


class PackagesClosureTest(unittest.TestCase):
    def package(self) -> DesignPackage:
        return DesignPackage(
            package_id="community.sync.fifo",
            version="1.0.0",
            design_id="sync.fifo",
            license_id="Apache-2.0",
            trust=TrustLevel.SIMULATION_VERIFIED,
            ports=(
                InterfacePort("clk", PortDirection.INPUT, 1),
                InterfacePort("wr_data", PortDirection.INPUT, 32),
                InterfacePort("rd_data", PortDirection.OUTPUT, 32),
            ),
            parameters=(Parameter("depth", 16, 2, 1024),),
            files=(PackageFile("rtl/sync_fifo.sv", "rtl", DIGEST),),
            evidence_ids=("ev.fifo.regression",),
        )

    def test_package_digest_and_compatibility_are_deterministic(self) -> None:
        package = self.package()
        report = analyze_compatibility(
            package,
            (
                InterfaceRequirement("clk", PortDirection.INPUT, 1),
                InterfaceRequirement("rd_data", PortDirection.OUTPUT, 32),
            ),
            (("depth", 32),),
        )
        self.assertTrue(report.compatible)
        self.assertTrue(package.publication_ready)
        self.assertEqual(package.content_digest, self.package().content_digest)

    def test_incompatible_package_reports_each_reason(self) -> None:
        report = analyze_compatibility(
            self.package(),
            (
                InterfaceRequirement("rd_data", PortDirection.OUTPUT, 16),
                InterfaceRequirement("empty", PortDirection.OUTPUT, 1),
            ),
            (("depth", 1),),
        )
        self.assertFalse(report.compatible)
        self.assertEqual(
            report.reasons,
            ("width mismatch for rd_data", "missing port empty", "parameter depth is below minimum"),
        )

    def test_closure_escalates_after_equivalent_failures(self) -> None:
        tracker = ClosureTracker(ClosurePolicy())
        for attempt in range(1, 4):
            tracker.record(
                RepairAttempt(
                    attempt,
                    "scoreboard.order.mismatch",
                    f"Investigate pointer path {attempt}",
                    attempt,
                )
            )
        decision, report = tracker.decide()
        self.assertIs(decision, ClosureDecision.ESCALATE)
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.reason, "equivalent_failure_limit")


if __name__ == "__main__":
    unittest.main()
