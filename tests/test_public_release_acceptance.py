from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import validate_public_release as public_release


class PublicReleaseAcceptanceTest(unittest.TestCase):
    def _manifest(self) -> bytes:
        document = {
            "artifacts": [
                {
                    "filename": "openrtl-examples-0.2.0.tar.gz",
                    "media_type": "application/gzip",
                    "sha256": "4e0961c2f560b8b202fbd412380bcaee38eae95a8a132047e93f6f411bf7c614",
                    "size_bytes": 16643,
                },
                {
                    "filename": "openrtl-0.2.0-py3-none-any.whl",
                    "media_type": "application/zip",
                    "sha256": "00af29b0bebdb409f7f4eb191c3a4330ca825ee3d5a07eb2d87c88379f2bc072",
                    "size_bytes": 103934,
                },
                {
                    "filename": "openrtl-0.2.0.tar.gz",
                    "media_type": "application/gzip",
                    "sha256": "6f92af2f28f3e450b466c748d4fb2561e57e84fff1ebb8771dd73dff810d8c51",
                    "size_bytes": 93505,
                },
            ],
            "distribution": "openrtl",
            "qualified_commit": public_release.QUALIFIED_COMMIT,
            "schema": "openrtl-release-manifest.v1",
            "tag_created": False,
            "tag_planned": "v0.2.0",
            "version": "0.2.0",
        }
        return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()

    def test_exact_published_manifest_is_accepted(self) -> None:
        content = self._manifest()
        self.assertEqual(public_release._sha256(content), public_release.RELEASE_MANIFEST_SHA256)
        artifacts = public_release.parse_release_manifest(content)
        self.assertEqual(len(artifacts), 3)
        self.assertEqual(artifacts[0].filename, "openrtl-examples-0.2.0.tar.gz")

    def test_tampered_manifest_fails_before_parsing(self) -> None:
        content = self._manifest().replace(b'"version": "0.2.0"', b'"version": "0.2.1"')
        with self.assertRaisesRegex(public_release.PublicReleaseValidationError, "digest"):
            public_release.parse_release_manifest(content)

    def test_only_https_github_hosts_are_trusted(self) -> None:
        self.assertTrue(public_release._trusted_github_url("https://github.com/mtmoreira/openrtl"))
        self.assertTrue(public_release._trusted_github_url("https://objects.githubusercontent.com/a"))
        self.assertFalse(public_release._trusted_github_url("http://github.com/mtmoreira/openrtl"))
        self.assertFalse(public_release._trusted_github_url("https://github.com.example.invalid/a"))

    def test_archive_path_traversal_and_links_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            traversal = tarfile.TarInfo("openrtl-examples-0.2.0/../outside")
            traversal.size = 0
            with self.assertRaisesRegex(public_release.PublicReleaseValidationError, "escapes"):
                public_release._archive_target(root, traversal, "openrtl-examples-0.2.0")
            link = tarfile.TarInfo("openrtl-examples-0.2.0/link")
            link.type = tarfile.SYMTYPE
            with self.assertRaisesRegex(public_release.PublicReleaseValidationError, "non-regular"):
                public_release._archive_target(root, link, "openrtl-examples-0.2.0")

    def test_examples_extraction_requires_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "examples.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                content = b"example\n"
                member = tarfile.TarInfo("openrtl-examples-0.2.0/examples/__init__.py")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            with self.assertRaisesRegex(public_release.PublicReleaseValidationError, "verifier"):
                public_release.extract_examples(archive_path, root / "output")

    def test_download_rejects_untrusted_url_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(public_release, "urlopen") as opener:
                with self.assertRaisesRegex(public_release.PublicReleaseValidationError, "allowed"):
                    public_release._download(
                        "https://example.invalid/release.whl",
                        Path(directory) / "release.whl",
                        maximum_bytes=100,
                    )
                opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
