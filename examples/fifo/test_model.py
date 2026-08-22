from __future__ import annotations

import random
import unittest

from examples.fifo.model import SyncFifoModel


class SyncFifoModelTest(unittest.TestCase):
    def test_reset_fill_backpressure_drain_and_order(self) -> None:
        fifo = SyncFifoModel(width=8, depth=3)
        self.assertTrue(fifo.cycle(rst_n=False).empty)
        for value in (10, 20, 30):
            result = fifo.cycle(wr_valid=True, wr_data=value)
            self.assertTrue(result.write_accepted)
        blocked = fifo.cycle(wr_valid=True, wr_data=40)
        self.assertFalse(blocked.write_accepted)
        self.assertTrue(blocked.full)
        observed = []
        for _ in range(3):
            result = fifo.cycle(rd_ready=True)
            self.assertTrue(result.read_accepted)
            observed.append(result.rd_data)
        self.assertEqual(observed, [10, 20, 30])

    def test_simultaneous_transfer_at_full_and_pointer_wrap_semantics(self) -> None:
        fifo = SyncFifoModel(width=4, depth=3)
        for value in (1, 2, 3):
            fifo.cycle(wr_valid=True, wr_data=value)
        for replacement, expected in ((4, 1), (5, 2), (6, 3), (7, 4)):
            result = fifo.cycle(wr_valid=True, wr_data=replacement, rd_ready=True)
            self.assertTrue(result.write_accepted)
            self.assertTrue(result.read_accepted)
            self.assertEqual(result.rd_data, expected)
            self.assertEqual(result.level, 3)

    def test_randomized_model_matches_independent_queue(self) -> None:
        rng = random.Random(17)
        fifo = SyncFifoModel(width=5, depth=5)
        expected: list[int] = []
        for _ in range(500):
            wr_valid = bool(rng.getrandbits(1))
            rd_ready = bool(rng.getrandbits(1))
            wr_data = rng.randrange(256)
            old_head = expected[0] if expected else 0
            read = bool(expected) and rd_ready
            ready = len(expected) < 5 or read
            write = wr_valid and ready
            result = fifo.cycle(wr_valid=wr_valid, wr_data=wr_data, rd_ready=rd_ready)
            self.assertEqual((result.wr_ready, result.rd_valid), (ready, bool(expected)))
            self.assertEqual(result.rd_data, old_head)
            if read:
                expected.pop(0)
            if write:
                expected.append(wr_data & 0x1F)
            self.assertEqual(fifo.contents, tuple(expected))
            self.assertEqual((result.empty, result.full), (not expected, len(expected) == 5))


if __name__ == "__main__":
    unittest.main()
