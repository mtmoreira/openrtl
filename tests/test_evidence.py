from __future__ import annotations

import unittest

from openrtl.domain import SourceAnchor, WaveformAnchor


DIGEST = "sha256:" + "d" * 64


class EvidenceTest(unittest.TestCase):
    def test_source_and_waveform_anchors_are_bounded(self) -> None:
        source = SourceAnchor("rtl/fifo.sv", 10, 14, DIGEST)
        waveform = WaveformAnchor(
            trace_id="trace.fifo.1",
            start_fs=1000,
            end_fs=5000,
            signals=("clk", "wr_ptr", "rd_ptr"),
            markers_fs=(2000, 4000),
        )
        self.assertEqual(source.line_start, 10)
        self.assertEqual(waveform.markers_fs, (2000, 4000))

    def test_invalid_source_or_waveform_ranges_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "source line range"):
            SourceAnchor("rtl/fifo.sv", 0, 2, DIGEST)
        with self.assertRaisesRegex(ValueError, "waveform interval"):
            WaveformAnchor("trace.fifo.1", 10, 10, ("clk",))
        with self.assertRaisesRegex(ValueError, "inside the waveform"):
            WaveformAnchor("trace.fifo.1", 10, 20, ("clk",), (21,))


if __name__ == "__main__":
    unittest.main()
