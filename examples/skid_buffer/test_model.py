from __future__ import annotations

import unittest

from examples.skid_buffer.model import SkidBufferModel


class SkidBufferModelTest(unittest.TestCase):
    def test_transparent_transfer_does_not_fill_buffer(self) -> None:
        model = SkidBufferModel(width=8)
        result = model.cycle(s_valid=True, s_data=0x31, m_ready=True)
        self.assertTrue(result.s_ready)
        self.assertTrue(result.m_valid)
        self.assertEqual(result.m_data, 0x31)
        self.assertTrue(result.input_accepted)
        self.assertTrue(result.output_accepted)
        self.assertFalse(result.occupied_after)

    def test_backpressure_capture_and_same_edge_refill_preserve_order(self) -> None:
        model = SkidBufferModel(width=8)
        captured = model.cycle(s_valid=True, s_data=0x11, m_ready=False)
        self.assertTrue(captured.occupied_after)
        refill = model.cycle(s_valid=True, s_data=0x22, m_ready=True)
        self.assertTrue(refill.s_ready)
        self.assertEqual(refill.m_data, 0x11)
        self.assertTrue(refill.input_accepted)
        self.assertTrue(refill.output_accepted)
        self.assertTrue(refill.occupied_after)
        drained = model.cycle(m_ready=True)
        self.assertEqual(drained.m_data, 0x22)
        self.assertTrue(drained.output_accepted)
        self.assertFalse(drained.occupied_after)

    def test_reset_discards_retained_word(self) -> None:
        model = SkidBufferModel(width=8)
        model.cycle(s_valid=True, s_data=0x5A, m_ready=False)
        result = model.cycle(rst_n=False)
        self.assertFalse(result.occupied_after)
        self.assertFalse(model.occupied)


if __name__ == "__main__":
    unittest.main()
