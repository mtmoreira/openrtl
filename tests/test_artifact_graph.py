from __future__ import annotations

import unittest

from openrtl.domain import ArtifactGraph, ArtifactKind, ArtifactRef, ArtifactRevision


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


class ArtifactGraphTest(unittest.TestCase):
    def test_tracks_latest_revision_and_transitive_dependents(self) -> None:
        graph = ArtifactGraph()
        requirements = ArtifactRevision(
            ref=ArtifactRef("requirements", 1),
            kind=ArtifactKind.REQUIREMENTS,
            uri="design/requirements.md",
            content_digest=DIGEST_A,
            summary="FIFO requirements",
            requirement_ids=("req.fifo.order",),
        )
        rtl = ArtifactRevision(
            ref=ArtifactRef("fifo.rtl", 1),
            kind=ArtifactKind.RTL,
            uri="rtl/fifo.sv",
            content_digest=DIGEST_B,
            summary="FIFO implementation",
            requirement_ids=("req.fifo.order",),
            dependencies=(requirements.ref,),
        )
        graph.add(requirements)
        graph.add(rtl)

        self.assertEqual(graph.latest("fifo.rtl"), rtl)
        self.assertEqual(graph.affected_by(requirements.ref), (rtl.ref,))

        revised = ArtifactRevision(
            ref=ArtifactRef("requirements", 2),
            kind=ArtifactKind.REQUIREMENTS,
            uri="design/requirements.md",
            content_digest=DIGEST_B,
            summary="Revised FIFO requirements",
            requirement_ids=("req.fifo.order",),
            supersedes=requirements.ref,
        )
        graph.add(revised)
        self.assertEqual(graph.latest("requirements"), revised)

    def test_rejects_unknown_dependencies_and_revision_gaps(self) -> None:
        graph = ArtifactGraph()
        with self.assertRaisesRegex(ValueError, "unknown artifact dependency"):
            graph.add(
                ArtifactRevision(
                    ref=ArtifactRef("fifo.rtl", 1),
                    kind=ArtifactKind.RTL,
                    uri="rtl/fifo.sv",
                    content_digest=DIGEST_A,
                    summary="FIFO",
                    dependencies=(ArtifactRef("requirements", 1),),
                )
            )


if __name__ == "__main__":
    unittest.main()
