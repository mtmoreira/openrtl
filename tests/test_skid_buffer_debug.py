from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from examples.skid_buffer.faults.ready_refill import render_skid_buffer_trace
from openrtl.adapters import (
    analyze_skid_buffer_waveform,
    propose_skid_buffer_repairs,
    skid_buffer_repair_focus,
    surfer_command_file,
)
from openrtl.application import DebugSessionReport


class SkidBufferDebugTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        (self.root / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.root / "build")
        self.output = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _report(self, *, fault: bool) -> DebugSessionReport:
        trace = self.output / ("fault.vcd" if fault else "passing.vcd")
        trace.write_text(
            render_skid_buffer_trace(refill_ready_fault=fault),
            encoding="utf-8",
        )
        return analyze_skid_buffer_waveform(
            self.root,
            trace,
            start_fs=10_000_000,
            end_fs=40_000_000,
            rtl_path=Path("examples/skid_buffer/rtl/skid_buffer.sv"),
        )

    def test_refill_fault_is_edge_linked_and_repair_is_non_applying(self) -> None:
        report = self._report(fault=True)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertIn(".refill-ready.25000000", finding.finding_id)
        self.assertEqual(finding.requirement_id, "skid.refill")
        self.assertEqual(finding.expected, "1")
        self.assertEqual(finding.observed, "0")
        proposal = propose_skid_buffer_repairs(
            report,
            report_uri="build/skid/debug-session.json",
        )
        self.assertFalse(proposal.payload()["applies_changes"])
        self.assertEqual(proposal.changes[0].finding_ids, (finding.finding_id,))
        focus = skid_buffer_repair_focus(report)
        self.assertIn("skid_buffer.s_ready", focus.signals)
        self.assertIn(25_000_000, focus.markers_fs)
        commands = surfer_command_file(focus)
        self.assertIn("skid_buffer.s_ready", commands)
        json.dumps(report.payload(), sort_keys=True)
        json.dumps(proposal.payload(), sort_keys=True)

    def test_correct_refill_trace_passes(self) -> None:
        report = self._report(fault=False)
        self.assertTrue(report.passed)
        self.assertEqual(report.findings, ())
        refill = next(
            value for value in report.observations if value.timestamp_fs == 25_000_000
        )
        self.assertEqual(refill.event, "simultaneous-transfer")

    def test_missing_required_signal_fails_closed(self) -> None:
        trace = self.output / "incomplete.vcd"
        trace.write_text(
            render_skid_buffer_trace(refill_ready_fault=True).replace(
                "$var wire 1 $ s_ready $end\n",
                "",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(KeyError, "unknown waveform signal"):
            analyze_skid_buffer_waveform(self.root, trace)


if __name__ == "__main__":
    unittest.main()
