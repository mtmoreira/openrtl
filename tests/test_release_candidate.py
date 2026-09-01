from __future__ import annotations

from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.validate_release import ReleaseValidationError
from tools.validate_release_candidate import (
    PUBLIC_AGENTRIG_COMMIT,
    PUBLIC_AGENTRIG_REPOSITORY,
    PUBLIC_AGENTRIG_TAG,
    candidate_environment,
    candidate_python_executable,
    extract_examples,
    validate_candidate_metadata,
    validate_public_agentrig_checkout,
)
from tools.validate_release import ReleaseManifest


class ReleaseCandidateTest(unittest.TestCase):
    def test_candidate_environment_exposes_venv_tools_and_drops_python_overrides(self) -> None:
        with patch.dict(
            "os.environ",
            {"PATH": "/host/bin", "PYTHONHOME": "/host/python", "PYTHONPATH": "/host/src"},
            clear=True,
        ):
            environment = candidate_environment(Path("/candidate/bin/python"))
        self.assertEqual(environment["PATH"], "/candidate/bin:/host/bin")
        self.assertNotIn("PYTHONHOME", environment)
        self.assertNotIn("PYTHONPATH", environment)

    def test_candidate_python_preserves_virtualenv_symlink_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "base-python"
            target.write_bytes(b"python")
            link = root / "candidate-venv-python"
            link.symlink_to(target)
            self.assertEqual(candidate_python_executable(link), link.absolute())
            self.assertNotEqual(candidate_python_executable(link), link.resolve())

    def test_candidate_metadata_requires_exact_agentrig_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "openrtl"\nversion = "0.3.0"\ndependencies = ["agentrig==0.3.0"]\n',
                encoding="utf-8",
            )
            validate_candidate_metadata(root, ReleaseManifest("0.3.0", "0" * 40, ()))
            (root / "pyproject.toml").write_text(
                '[project]\nname = "openrtl"\nversion = "0.3.0"\ndependencies = ["agentrig>=0.3.0"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReleaseValidationError, "pin exactly"):
                validate_candidate_metadata(root, ReleaseManifest("0.3.0", "0" * 40, ()))

    def test_public_agentrig_checkout_binds_repository_tag_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            values = {
                ("remote", "get-url", "origin"): PUBLIC_AGENTRIG_REPOSITORY,
                ("rev-parse", "HEAD"): PUBLIC_AGENTRIG_COMMIT,
                ("rev-parse", f"{PUBLIC_AGENTRIG_TAG}^{{commit}}"): PUBLIC_AGENTRIG_COMMIT,
            }
            with patch("tools.validate_release_candidate._git", side_effect=lambda _root, *args: values[args]):
                validate_public_agentrig_checkout(source)

    def test_public_agentrig_identity_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("tools.validate_release_candidate._git", return_value="unexpected"):
                with self.assertRaisesRegex(ReleaseValidationError, "public repository"):
                    validate_public_agentrig_checkout(Path(temporary))

    def test_examples_extraction_accepts_only_regular_prefixed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "examples.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                data = b"example\n"
                info = tarfile.TarInfo("openrtl-examples-0.3.0/examples/example.py")
                info.size = len(data)
                import io
                archive.addfile(info, io.BytesIO(data))
            extracted = extract_examples(archive_path, root / "output", "0.3.0")
            self.assertEqual((extracted / "examples/example.py").read_bytes(), b"example\n")

    def test_unsafe_examples_member_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "examples.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                import io
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ReleaseValidationError, "unsafe"):
                extract_examples(archive_path, root / "output", "0.3.0")


if __name__ == "__main__":
    unittest.main()
