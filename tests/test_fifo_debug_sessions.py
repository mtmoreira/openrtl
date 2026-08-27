from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from openrtl.adapters import analyze_fifo_waveform
from openrtl.cli import main


def _fifo_trace(*, broken_level: bool = False) -> str:
    level_at_full = "01" if broken_level else "10"
    full_at_full = "0" if broken_level else "1"
    return (
        "$timescale 1 ns $end\n"
        "$scope module sync_fifo $end\n"
        "$var wire 1 ! clk $end\n"
        '$var wire 1 " rst_n $end\n'
        "$var wire 1 # wr_valid $end\n"
        "$var wire 1 $ wr_ready $end\n"
        "$var wire 1 % write_accepted $end\n"
        "$var wire 8 & wr_data [7:0] $end\n"
        "$var wire 1 ' rd_valid $end\n"
        "$var wire 1 ( rd_ready $end\n"
        "$var wire 1 ) read_accepted $end\n"
        "$var wire 8 * rd_data [7:0] $end\n"
        "$var wire 2 + level [1:0] $end\n"
        "$var wire 1 , full $end\n"
        "$var wire 1 - empty $end\n"
        "$var wire 1 . write_pointer $end\n"
        "$var wire 1 / read_pointer $end\n"
        "$var wire 32 0 DEPTH [31:0] $end\n"
        "$upscope $end\n"
        "$enddefinitions $end\n"
        "#0\n0!\n1\"\n1#\n1$\n1%\nb00001010 &\n0'\n0(\n0)\n"
        "b00000000 *\nb00 +\n0,\n1-\n0.\n0/\n"
        "b00000000000000000000000000000010 0\n"
        "#5\n1!\nb01 +\n0-\n1'\n1.\nb00001010 *\n"
        "#6\nb00001011 &\n1(\n1)\n"
        "#10\n0!\n"
        "#15\n1!\n0.\n1/\nb00001011 *\n"
        "#16\nb00001100 &\n0(\n0)\n"
        "#20\n0!\n"
        f"#25\n1!\nb{level_at_full} +\n{full_at_full},\n1.\n0$\n0%\n"
        "#26\nb00001101 &\n"
        "#30\n0!\n"
        "#35\n1!\n"
        "#36\n1(\n1)\n1$\n1%\n"
        "#40\n0!\n"
        "#45\n1!\n0.\n0/\nb00001100 *\n"
        "#50\n0!\n"
    )


class FifoDebugSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.trace = self.root / "waves.vcd"
        rtl = self.root / "examples/fifo/rtl/sync_fifo.sv"
        rtl.parent.mkdir(parents=True)
        rtl.write_text(
            "module sync_fifo;\n"
            "assign read_accepted = rd_valid && rd_ready;\n"
            "assign wr_ready = !full || read_accepted;\n"
            "assign write_accepted = wr_valid && wr_ready;\n"
            "always_ff @(posedge clk) begin\n"
            "  unique case ({write_accepted, read_accepted})\n"
            "  endcase\n"
            "end\n"
            "endmodule\n",
            encoding="utf-8",
        )

    def test_passing_trace_explains_transfers_backpressure_and_wrap(self) -> None:
        self.trace.write_text(_fifo_trace(), encoding="utf-8")

        report = analyze_fifo_waveform(self.root, self.trace)

        self.assertTrue(report.passed)
        self.assertEqual(dict(report.metadata)["depth"], "2")
        self.assertEqual(
            [value.event for value in report.observations],
            [
                "write-transfer",
                "simultaneous-transfer",
                "write-transfer",
                "write-blocked",
                "simultaneous-transfer",
            ],
        )
        final = report.observations[-1]
        self.assertEqual(dict(final.signal_values)["rd_data"], "0xb")
        self.assertEqual(dict(final.signal_values)["level_after"], "2")
        self.assertEqual(report.waveform_anchor.markers_fs, (5_000_000, 15_000_000, 25_000_000, 35_000_000, 45_000_000))

    def test_level_bug_produces_requirement_linked_waveform_finding(self) -> None:
        self.trace.write_text(_fifo_trace(broken_level=True), encoding="utf-8")

        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
        )

        self.assertFalse(report.passed)
        level = next(value for value in report.findings if ".level." in value.finding_id)
        self.assertEqual(level.requirement_id, "fifo.write")
        self.assertEqual(level.expected, "2")
        self.assertEqual(level.observed, "1")
        self.assertEqual(level.waveform_anchor.markers_fs, (25_000_000,))

    def test_reset_edge_is_explained_as_reset_even_with_a_handshake_request(self) -> None:
        reset_trace = _fifo_trace().replace(
            '#0\n0!\n1"\n1#',
            '#0\n0!\n0"\n1#',
            1,
        )
        self.trace.write_text(reset_trace, encoding="utf-8")

        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=0,
            end_fs=5_000_000,
        )

        self.assertEqual(report.observations[0].event, "reset-edge")
        self.assertEqual(report.observations[0].requirement_ids, ("fifo.reset",))

    def test_cli_writes_reviewable_debug_session_and_returns_finding_status(self) -> None:
        self.trace.write_text(_fifo_trace(), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "waveform",
                    "diagnose-fifo",
                    "waves.vcd",
                    "--root",
                    str(self.root),
                    "--output",
                    "build/debug/session.json",
                )
            )

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        retained = json.loads(
            (self.root / "build/debug/session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload, retained)
        self.assertEqual(payload["schema"], "openrtl.debug-session.v1")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["waveform_anchor"]["trace_id"], f"{payload['session_id']}.trace")
        self.assertGreaterEqual(len(payload["source_anchors"]), 5)

    def test_unknown_signal_and_edge_free_window_fail_closed(self) -> None:
        self.trace.write_text(_fifo_trace(), encoding="utf-8")
        with self.assertRaisesRegex(KeyError, "unknown waveform signal"):
            analyze_fifo_waveform(self.root, self.trace, hierarchy="other")
        with self.assertRaisesRegex(ValueError, "no rising"):
            analyze_fifo_waveform(
                self.root,
                self.trace,
                start_fs=1_000_000,
                end_fs=4_000_000,
            )


if __name__ == "__main__":
    unittest.main()
