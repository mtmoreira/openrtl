from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openrtl.adapters import inspect_vcd, surfer_command_file
from openrtl.cli import main


_TRACE = (
    "$timescale 1 ns $end\n"
    "$scope module sync_fifo $end\n"
    "$var wire 1 ! clk $end\n"
    "$var wire 1 \" wr_valid $end\n"
    "$var wire 3 # level $end\n"
    "$upscope $end\n"
    "#0\n0!\n0\"\nb000 #\n"
    "#5\n1!\n1\"\nb001 #\n"
    "#10\n0!\n0\"\nb010 #\n"
)


class WaveformWorkbenchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trace = self.root / "waves.vcd"
        self.trace.write_text(_TRACE, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inspection_lists_signals_values_and_bounded_transitions(self) -> None:
        index, report = inspect_vcd(
            self.root,
            Path("waves.vcd"),
            signals=("sync_fifo.wr_valid", "sync_fifo.level"),
            start_fs=5_000_000,
            end_fs=10_000_000,
            max_transitions=1,
        )

        self.assertEqual(index.end_time_fs, 10_000_000)
        self.assertEqual(index.value_before("sync_fifo.level", 5_000_000), "000")
        self.assertEqual(index.value_at("sync_fifo.level", 5_000_000), "001")
        self.assertEqual(report.selected_signals[0].value_at_start, "1")
        self.assertTrue(report.selected_signals[0].truncated)
        self.assertEqual(
            report.selected_signals[1].transitions[0].value,
            "001",
        )

    def test_focus_command_file_uses_surfer_07_subset_and_fs_metadata(self) -> None:
        index, report = inspect_vcd(
            self.root,
            self.trace,
            signals=("sync_fifo.wr_valid", "sync_fifo.level"),
        )
        focus = index.focus(
            report.trace,
            ("sync_fifo.wr_valid", "sync_fifo.level"),
            0,
            report.end_fs,
        )

        self.assertEqual(
            surfer_command_file(focus),
            "# OpenRTL focus metadata; values are integer femtoseconds.\n"
            "# focus-window-fs: 0 10000000\n"
            "# focus-markers-fs: 0,5000000,10000000\n"
            "# Surfer 0.7: set the viewport manually from the metadata above.\n"
            "variable_add sync_fifo.wr_valid\n"
            "variable_add sync_fifo.level\n"
        )
        self.assertNotIn("zoom_to", surfer_command_file(focus))
        self.assertNotIn("cursor_set", surfer_command_file(focus))
        self.assertNotIn("marker_set_at", surfer_command_file(focus))

    def test_cli_focus_preserves_reviewable_collateral_without_launch(self) -> None:
        status = main(
            (
                "waveform",
                "focus",
                "waves.vcd",
                "--root",
                str(self.root),
                "--signal",
                "sync_fifo.wr_valid",
                "--signal",
                "sync_fifo.level",
                "--output-directory",
                "build/debug",
            )
        )

        self.assertEqual(status, 0)
        report = json.loads(
            (self.root / "build/debug/inspection.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["schema"], "openrtl.waveform-inspection.v1")
        self.assertIsNone(report.get("launched_process_id"))
        self.assertTrue((self.root / "build/debug/focus.sucl").is_file())

    def test_trace_outside_root_and_invalid_window_fail_closed(self) -> None:
        outside = self.root.parent / "outside.vcd"
        outside.write_text(_TRACE, encoding="utf-8")
        self.addCleanup(outside.unlink)

        with self.assertRaisesRegex(ValueError, "contained"):
            inspect_vcd(self.root, outside)
        with self.assertRaisesRegex(ValueError, "interval"):
            inspect_vcd(self.root, self.trace, start_fs=10, end_fs=9)


if __name__ == "__main__":
    unittest.main()
