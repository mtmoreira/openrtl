"""Build and validate an immutable OpenRTL release candidate."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
from typing import IO, cast
from zipfile import BadZipFile, ZipFile


RELEASE_MANIFEST_SCHEMA = "openrtl-release-manifest.v1"
_NAME = "openrtl"
_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN = frozenset({".env", ".git", ".git-credentials", ".netrc", ".npmrc", ".pypirc", "AGENTS.md", "SKILL.md", "__pycache__"})
_FORBIDDEN_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx", ".pyc"})
_EXAMPLE_FILES = (
    "examples/__init__.py",
    "examples/fifo/README.md",
    "examples/fifo/__init__.py",
    "examples/fifo/spec.md",
    "examples/fifo/model.py",
    "examples/fifo/test_model.py",
    "examples/fifo/rtl/sync_fifo.sv",
    "examples/fifo/dv/Makefile",
    "examples/fifo/dv/test_sync_fifo.py",
    "examples/fifo/dv/test_fifo_level_repair.py",
    "examples/fifo/faults/README.md",
    "examples/fifo/faults/__init__.py",
    "examples/fifo/faults/level_update.py",
    "examples/fifo/faults/level_update_edit_spec.json",
    "examples/fifo/faults/sync_fifo_level_fault.sv",
    "examples/fifo/faults/sync_fifo_level_fault_fixture.sv",
    "tools/__init__.py",
    "tools/fifo_fault_case.py",
    "tools/fifo_repair_application_case.py",
    "tools/verify_release_install.py",
    "tools/verilator_canary.py",
    "examples/fifo/verified-profile.json",
    "examples/skid_buffer/__init__.py",
    "examples/skid_buffer/README.md",
    "examples/skid_buffer/spec.md",
    "examples/skid_buffer/model.py",
    "examples/skid_buffer/test_model.py",
    "examples/skid_buffer/verified-profile.json",
    "examples/skid_buffer/rtl/skid_buffer.sv",
    "examples/skid_buffer/dv/Makefile",
    "examples/skid_buffer/dv/test_skid_buffer.py",
    "examples/skid_buffer/faults/__init__.py",
    "examples/skid_buffer/faults/ready_refill.py",
    "examples/skid_buffer/faults/skid_buffer_refill_fault.sv",
    "examples/composed_stream/README.md",
    "examples/composed_stream/rtl/fifo_skid_stream.sv",
    "examples/composed_stream/dv/Makefile",
    "examples/composed_stream/dv/test_composed_stream.py",
    "tools/skid_buffer_case.py",
    "tools/composed_package_case.py",
    "tools/composed_package_matrix.py",
    "tools/verify_release_examples_v040.py",
)


class ReleaseValidationError(ValueError):
    """A release candidate violates the checked distribution contract."""


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    filename: str
    sha256: str
    size_bytes: int
    media_type: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "filename": self.filename,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    qualified_commit: str
    artifacts: tuple[ReleaseArtifact, ...]

    @property
    def filename(self) -> str:
        return f"openrtl-{self.version}-release.json"

    def to_json(self) -> str:
        return json.dumps(
            {
                "artifacts": [artifact.to_dict() for artifact in self.artifacts],
                "distribution": _NAME,
                "qualified_commit": self.qualified_commit,
                "schema": RELEASE_MANIFEST_SCHEMA,
                "tag_created": False,
                "tag_planned": f"v{self.version}",
                "version": self.version,
            },
            indent=2,
            sort_keys=True,
        ) + "\n"


def build_examples_archive(root: Path, output_directory: Path) -> Path:
    """Create the normalized companion archive from the checked example set."""
    project = _project(root)
    version = _text(project.get("version"), "project version")
    output_directory.mkdir(parents=True, exist_ok=True)
    output = output_directory / f"openrtl-examples-{version}.tar.gz"
    missing = tuple(
        path
        for path in _EXAMPLE_FILES
        if not (root / path).is_file() or (root / path).is_symlink()
    )
    if missing:
        raise ReleaseValidationError(f"example source is incomplete: {', '.join(missing)}")
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            _write_examples_tar(root, cast(IO[bytes], compressed), version)
    return output


def _write_examples_tar(root: Path, stream: IO[bytes], version: str) -> None:
    prefix = f"openrtl-examples-{version}"
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative in _EXAMPLE_FILES:
            source = root / relative
            data = source.read_bytes()
            info = tarfile.TarInfo(f"{prefix}/{relative}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            import io
            archive.addfile(info, io.BytesIO(data))


def validate_release(root: Path, dist: Path, *, commit: str) -> ReleaseManifest:
    """Validate wheel, sdist, and examples archive against exact metadata."""
    project = _project(root)
    name = _text(project.get("name"), "project name")
    version = _text(project.get("version"), "project version")
    if name != _NAME or _VERSION.fullmatch(version) is None:
        raise ReleaseValidationError("project identity must be openrtl with a stable version")
    if _COMMIT.fullmatch(commit) is None:
        raise ReleaseValidationError("qualified commit must be a full lowercase SHA")
    if not dist.is_dir():
        raise ReleaseValidationError("distribution directory is missing")
    expected_names = {
        f"openrtl-{version}-py3-none-any.whl",
        f"openrtl-{version}.tar.gz",
        f"openrtl-examples-{version}.tar.gz",
        f"openrtl-{version}-release.json",
    }
    unexpected = tuple(sorted(path.name for path in dist.iterdir() if path.name not in expected_names))
    if unexpected:
        raise ReleaseValidationError(f"distribution directory contains unexpected artifacts: {', '.join(unexpected)}")
    wheel = _one(dist, f"openrtl-{version}-py3-none-any.whl")
    sdist = _one(dist, f"openrtl-{version}.tar.gz")
    examples = _one(dist, f"openrtl-examples-{version}.tar.gz")
    _validate_wheel(wheel, project)
    _validate_sdist(sdist, project, version)
    _validate_examples(examples, version)
    return ReleaseManifest(
        version=version,
        qualified_commit=commit,
        artifacts=(
            _artifact(examples, "application/gzip"),
            _artifact(wheel, "application/zip"),
            _artifact(sdist, "application/gzip"),
        ),
    )


def write_manifest(manifest: ReleaseManifest, dist: Path) -> Path:
    output = dist / manifest.filename
    content = manifest.to_json()
    if output.exists() and output.read_text(encoding="utf-8") != content:
        raise ReleaseValidationError("release manifest exists with different content")
    output.write_text(content, encoding="utf-8")
    return output


def _project(root: Path) -> Mapping[str, object]:
    with (root / "pyproject.toml").open("rb") as source:
        document = tomllib.load(source)
    project = document.get("project")
    if not isinstance(project, Mapping):
        raise ReleaseValidationError("pyproject.toml has no project table")
    return cast(Mapping[str, object], project)


def _one(dist: Path, expected: str) -> Path:
    candidates = tuple(sorted(path for path in dist.iterdir() if path.name == expected))
    if len(candidates) != 1:
        raise ReleaseValidationError(f"release requires exactly one {expected}")
    if not candidates[0].is_file() or candidates[0].is_symlink():
        raise ReleaseValidationError(f"release artifact must be a regular file: {expected}")
    return candidates[0]


def _validate_wheel(path: Path, project: Mapping[str, object]) -> None:
    with ZipFile(path) as archive:
        names = tuple(item.filename for item in archive.infolist())
        _safe_names(names, "wheel")
        required = {"openrtl/__init__.py", "openrtl/py.typed"}
        if not required.issubset(names):
            raise ReleaseValidationError("wheel is missing OpenRTL package files")
        if any(name.startswith(("examples/", "tests/", "tools/")) for name in names):
            raise ReleaseValidationError("wheel contains repository-only top-level content")
        metadata_names = tuple(name for name in names if name.endswith(".dist-info/METADATA"))
        if len(metadata_names) != 1:
            raise ReleaseValidationError("wheel must contain one METADATA record")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_names[0]))
    _metadata(metadata, project, "wheel")


def _validate_sdist(path: Path, project: Mapping[str, object], version: str) -> None:
    prefix = f"openrtl-{version}/"
    with tarfile.open(path, mode="r:gz") as archive:
        members = tuple(archive.getmembers())
        names = tuple(member.name for member in members)
        _safe_names(names, "source distribution")
        if any(member.issym() or member.islnk() for member in members):
            raise ReleaseValidationError("source distribution contains links")
        required = {
            f"{prefix}PKG-INFO",
            f"{prefix}README.md",
            f"{prefix}pyproject.toml",
            f"{prefix}src/openrtl/__init__.py",
            *(f"{prefix}{name}" for name in _EXAMPLE_FILES),
        }
        if not required.issubset(names):
            raise ReleaseValidationError("source distribution is missing release examples")
        metadata_file = archive.extractfile(f"{prefix}PKG-INFO")
        if metadata_file is None:
            raise ReleaseValidationError("source distribution metadata is unreadable")
        metadata = BytesParser(policy=default).parsebytes(metadata_file.read())
    _metadata(metadata, project, "source distribution")


def _validate_examples(path: Path, version: str) -> None:
    prefix = f"openrtl-examples-{version}/"
    with tarfile.open(path, mode="r:gz") as archive:
        members = tuple(archive.getmembers())
        names = tuple(member.name for member in members)
        _safe_names(names, "examples archive")
        if any(not member.isfile() or member.issym() or member.islnk() for member in members):
            raise ReleaseValidationError("examples archive must contain regular files only")
        expected = tuple(f"{prefix}{name}" for name in _EXAMPLE_FILES)
        if names != expected:
            raise ReleaseValidationError("examples archive manifest is not exact")


def _metadata(message: Message, project: Mapping[str, object], source: str) -> None:
    if message.get("Name") != project.get("name") or message.get("Version") != project.get("version") or message.get("Requires-Python") != project.get("requires-python"):
        raise ReleaseValidationError(f"{source} metadata does not match pyproject.toml")
    expected_requirements = [str(value) for value in cast(list[object], project.get("dependencies", []))]
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, Mapping):
        raise ReleaseValidationError("optional dependency metadata is invalid")
    for extra in sorted(optional):
        requirements = optional[extra]
        if not isinstance(extra, str) or not isinstance(requirements, list):
            raise ReleaseValidationError("optional dependency metadata is invalid")
        expected_requirements.extend(f"{value} ; extra == '{extra}'" for value in requirements)
    if tuple(message.get_all("Requires-Dist", [])) != tuple(expected_requirements):
        raise ReleaseValidationError(f"{source} dependency metadata does not match pyproject.toml")
    if tuple(message.get_all("Provides-Extra", [])) != tuple(sorted(cast(Mapping[str, object], optional))):
        raise ReleaseValidationError(f"{source} extra metadata does not match pyproject.toml")


def _safe_names(names: Sequence[str], kind: str) -> None:
    if len(names) != len(set(names)):
        raise ReleaseValidationError(f"{kind} contains duplicate paths")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ReleaseValidationError(f"{kind} contains an unsafe path")
        if any(part in _FORBIDDEN or part.startswith(".env.") for part in path.parts):
            raise ReleaseValidationError(f"{kind} contains private or repository-only content")
        if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
            raise ReleaseValidationError(f"{kind} contains forbidden file content")


def _artifact(path: Path, media_type: str) -> ReleaseArtifact:
    return ReleaseArtifact(path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, media_type)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ReleaseValidationError(f"{field} must be non-empty text")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=root)
    parser.add_argument("--dist-dir", type=Path, default=root / "dist")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build-examples", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.build_examples:
            build_examples_archive(args.repository_root, args.dist_dir)
        manifest = validate_release(args.repository_root, args.dist_dir, commit=args.commit)
        if args.write:
            output = write_manifest(manifest, args.dist_dir)
            print(f"CHECKPOINT release_manifest written {output.name}")
        else:
            print(manifest.to_json(), end="")
    except (BadZipFile, OSError, ReleaseValidationError, tarfile.TarError, tomllib.TOMLDecodeError) as error:
        print(f"release validation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
