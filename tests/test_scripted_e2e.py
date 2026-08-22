from __future__ import annotations

import unittest
from pathlib import Path

from openrtl.application import FIFO_REQUIREMENTS, run_scripted_fifo
from openrtl.domain import InteractionMode, SourceAnchor, WaveformAnchor


class ScriptedEndToEndTest(unittest.TestCase):
    def test_build_mode_produces_fully_covered_local_package_candidate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = run_scripted_fifo(root, InteractionMode.BUILD)

        self.assertTrue(result.package.publication_ready)
        self.assertEqual(result.package.license_id, "Apache-2.0")
        self.assertEqual(tuple(row.requirement_id for row in result.coverage), tuple(sorted(FIFO_REQUIREMENTS)))
        self.assertTrue(all(row.covered for row in result.coverage))
        self.assertIsNone(result.learning)
        self.assertEqual(result.knowledge.run("fifo.scripted").status.value, "passed")

    def test_learn_mode_adds_source_and_waveform_linked_teaching(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = run_scripted_fifo(root, InteractionMode.LEARN)
        if result.learning is None:
            raise AssertionError("learn mode must create a learning session")
        step = result.learning.next_step()
        if step is None:
            raise AssertionError("learn mode must create a teaching step")
        self.assertTrue(any(isinstance(value, SourceAnchor) for value in step.anchors))
        self.assertTrue(any(isinstance(value, WaveformAnchor) for value in step.anchors))


if __name__ == "__main__":
    unittest.main()
