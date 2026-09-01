from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from tools import validate_public_release_v030 as public_release
from tools.validate_public_release import PublicReleaseValidationError


class PublicReleaseV030Test(unittest.TestCase):
    def _manifest(self) -> bytes:
        document = {
            "artifacts": [
                {
                    "filename": "openrtl-examples-0.3.0.tar.gz",
                    "media_type": "application/gzip",
                    "sha256": "9e79b1cf93027f3dffac34de2b0ade3592f302f1133158628e4632c738032653",
                    "size_bytes": 16841,
                },
                {
                    "filename": "openrtl-0.3.0-py3-none-any.whl",
                    "media_type": "application/zip",
                    "sha256": "9b226dd872e2f08f4e2ec95a81c0663038d0d7dc64117300f51e1b43de1159ee",
                    "size_bytes": 104305,
                },
                {
                    "filename": "openrtl-0.3.0.tar.gz",
                    "media_type": "application/gzip",
                    "sha256": "2db88017f9a06207f13a68aadfe3bd63bd0763a4e602440f4a7cd1ef3f71b598",
                    "size_bytes": 94081,
                },
            ],
            "distribution": "openrtl",
            "qualified_commit": public_release.QUALIFIED_COMMIT,
            "schema": "openrtl-release-manifest.v1",
            "tag_created": False,
            "tag_planned": "v0.3.0",
            "version": "0.3.0",
        }
        return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()

    def test_exact_published_manifest_is_accepted(self) -> None:
        content = self._manifest()
        self.assertEqual(hashlib.sha256(content).hexdigest(), public_release.RELEASE_MANIFEST_SHA256)
        artifacts = public_release.parse_release_manifest(content)
        self.assertEqual(
            {artifact.filename for artifact in artifacts},
            {
                "openrtl-0.3.0-py3-none-any.whl",
                "openrtl-0.3.0.tar.gz",
                "openrtl-examples-0.3.0.tar.gz",
            },
        )

    def test_tampered_manifest_fails_closed(self) -> None:
        content = self._manifest().replace(b'"version": "0.3.0"', b'"version": "0.3.1"')
        with self.assertRaisesRegex(PublicReleaseValidationError, "digest"):
            public_release.parse_release_manifest(content)

    def test_verification_command_binds_both_versions_and_verilator(self) -> None:
        command = public_release.verification_command(
            Path("/candidate/bin/python"),
            Path("/examples"),
            with_verilator=True,
        )
        self.assertIn("--expected-version", command)
        self.assertIn("--expected-agentrig-version", command)
        self.assertEqual(command[-1], "--with-verilator")
        self.assertEqual(command[command.index("--expected-version") + 1], "0.3.0")
        self.assertEqual(command[command.index("--expected-agentrig-version") + 1], "0.3.0")

    def test_release_and_dependency_tags_are_exact_public_contracts(self) -> None:
        self.assertEqual(public_release.TAG, "v0.3.0")
        self.assertEqual(public_release.RELEASE_COMMIT, "a69d27d645d351ade3a8974acf21c21b31c8dc5e")
        self.assertEqual(public_release.AGENTRIG_TAG, "v0.3.0")
        self.assertEqual(public_release.AGENTRIG_COMMIT, "31b2ecae0605f0d6b63b5f060c929ca567ae16f2")


if __name__ == "__main__":
    unittest.main()
