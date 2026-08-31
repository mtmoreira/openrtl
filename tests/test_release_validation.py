from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
import tarfile
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from tools.validate_release import (
    _EXAMPLE_FILES,
    ReleaseValidationError,
    build_examples_archive,
    validate_release,
    write_manifest,
)


_COMMIT = "0123456789abcdef0123456789abcdef01234567"


class ReleaseValidationTest(unittest.TestCase):
    def test_examples_archive_is_reproducible_and_exact(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            first = build_examples_archive(root, dist).read_bytes()
            (dist / "openrtl-examples-0.2.0.tar.gz").unlink()
            second = build_examples_archive(root, dist).read_bytes()
            self.assertEqual(first, second)
            with tarfile.open(dist / "openrtl-examples-0.2.0.tar.gz", "r:gz") as archive:
                self.assertEqual(
                    tuple(member.name for member in archive.getmembers()),
                    tuple(f"openrtl-examples-0.2.0/{name}" for name in _EXAMPLE_FILES),
                )

    def test_release_binds_wheel_sdist_and_examples(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            manifest = validate_release(root, dist, commit=_COMMIT)
            self.assertEqual(manifest.version, "0.2.0")
            self.assertEqual(
                tuple(artifact.filename for artifact in manifest.artifacts),
                (
                    "openrtl-examples-0.2.0.tar.gz",
                    "openrtl-0.2.0-py3-none-any.whl",
                    "openrtl-0.2.0.tar.gz",
                ),
            )
            document = manifest.to_json()
            self.assertIn('"tag_created": false', document)
            self.assertIn('"tag_planned": "v0.2.0"', document)

    def test_missing_example_fails_closed(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            (root / _EXAMPLE_FILES[-1]).unlink()
            (dist / "openrtl-examples-0.2.0.tar.gz").unlink()
            with self.assertRaisesRegex(ReleaseValidationError, "incomplete"):
                build_examples_archive(root, dist)

    def test_symlinked_example_fails_closed(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            target = root / _EXAMPLE_FILES[-1]
            replacement = root / "replacement.py"
            replacement.write_text("replacement\n", encoding="utf-8")
            target.unlink()
            target.symlink_to(replacement)
            (dist / "openrtl-examples-0.2.0.tar.gz").unlink()
            with self.assertRaisesRegex(ReleaseValidationError, "incomplete"):
                build_examples_archive(root, dist)

    def test_extra_examples_archive_member_is_rejected(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            archive_path = dist / "openrtl-examples-0.2.0.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                data = b"unexpected\n"
                info = tarfile.TarInfo("openrtl-examples-0.2.0/unexpected.txt")
                info.size = len(data)
                import io
                archive.addfile(info, io.BytesIO(data))
            with self.assertRaisesRegex(ReleaseValidationError, "not exact"):
                validate_release(root, dist, commit=_COMMIT)

    def test_manifest_write_is_idempotent_and_not_replaceable(self) -> None:
        with self._candidate() as candidate:
            root, dist = candidate
            manifest = validate_release(root, dist, commit=_COMMIT)
            output = write_manifest(manifest, dist)
            self.assertEqual(write_manifest(manifest, dist), output)
            output.write_text("different\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseValidationError, "different"):
                write_manifest(manifest, dist)

    def _candidate(self) -> _Candidate:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        dist = root / "dist"
        dist.mkdir()
        (root / "pyproject.toml").write_text(
            "[project]\nname = \"openrtl\"\nversion = \"0.2.0\"\nrequires-python = \">=3.12\"\ndependencies = [\"agentrig==0.2.2\"]\n\n[project.optional-dependencies]\nsimulation = [\"cocotb==2.0.1\"]\n",
            encoding="utf-8",
        )
        for relative in _EXAMPLE_FILES:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture: {relative}\n", encoding="utf-8")
        self._wheel(dist)
        self._sdist(root, dist)
        build_examples_archive(root, dist)
        return _Candidate(temporary, root, dist)

    def _metadata(self) -> bytes:
        message = EmailMessage()
        message["Metadata-Version"] = "2.4"
        message["Name"] = "openrtl"
        message["Version"] = "0.2.0"
        message["Requires-Python"] = ">=3.12"
        message["Requires-Dist"] = "agentrig==0.2.2"
        message["Requires-Dist"] = "cocotb==2.0.1 ; extra == 'simulation'"
        message["Provides-Extra"] = "simulation"
        return message.as_bytes()

    def _wheel(self, dist: Path) -> None:
        with ZipFile(dist / "openrtl-0.2.0-py3-none-any.whl", "w", ZIP_DEFLATED) as archive:
            archive.writestr("openrtl/__init__.py", '__version__ = "0.2.0"\n')
            archive.writestr("openrtl/py.typed", "")
            archive.writestr("openrtl-0.2.0.dist-info/METADATA", self._metadata())
            archive.writestr("openrtl-0.2.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
            archive.writestr("openrtl-0.2.0.dist-info/RECORD", "")

    def _sdist(self, root: Path, dist: Path) -> None:
        prefix = "openrtl-0.2.0"
        with tarfile.open(dist / "openrtl-0.2.0.tar.gz", "w:gz") as archive:
            files = {
                "PKG-INFO": self._metadata(),
                "README.md": b"# OpenRTL\n",
                "pyproject.toml": (root / "pyproject.toml").read_bytes(),
                "src/openrtl/__init__.py": b'__version__ = "0.2.0"\n',
                **{name: (root / name).read_bytes() for name in _EXAMPLE_FILES},
            }
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{prefix}/{relative}")
                info.size = len(data)
                import io
                archive.addfile(info, io.BytesIO(data))


class _Candidate:
    def __init__(self, temporary: tempfile.TemporaryDirectory[str], root: Path, dist: Path) -> None:
        self.temporary = temporary
        self.root = root
        self.dist = dist

    def __enter__(self) -> tuple[Path, Path]:
        return self.root, self.dist

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
