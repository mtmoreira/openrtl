"""Validate the published OpenRTL release from public inputs only."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from typing import Protocol, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen


VERSION = "0.2.0"
TAG = "v0.2.0"
QUALIFIED_COMMIT = "83fb441a29dd655397fc6cfd7615538c0aecde5a"
RELEASE_BASE_URL = "https://github.com/mtmoreira/openrtl/releases/download/v0.2.0"
RELEASE_PAGE_URL = "https://github.com/mtmoreira/openrtl/releases/tag/v0.2.0"
RELEASE_MANIFEST_NAME = "openrtl-0.2.0-release.json"
RELEASE_MANIFEST_SHA256 = "0bdc307bd5b4bd974fc6f5710f201d074fd83555b2dc974f2fbcb95af15a27fc"
AGENTRIG_REPOSITORY = "https://github.com/mtmoreira/agentrig.git"
AGENTRIG_COMMIT = "b03087d1040b40e1d7d1efc98439d501964567c6"
AGENTRIG_VERSION = "0.2.2"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class PublicReleaseValidationError(ValueError):
    """The published release violates its immutable acceptance contract."""


class _ReadableResponse(Protocol):
    def read(self, amount: int = -1) -> bytes: ...

    def geturl(self) -> str: ...

    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *arguments: object) -> None: ...


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    filename: str
    media_type: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "filename": self.filename,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublicReleaseValidationError(f"{label} is invalid")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PublicReleaseValidationError(f"{label} is invalid")
    return value


def parse_release_manifest(content: bytes) -> tuple[PublishedArtifact, ...]:
    """Parse the exact immutable v0.2.0 manifest."""
    if _sha256(content) != RELEASE_MANIFEST_SHA256:
        raise PublicReleaseValidationError("release manifest digest is invalid")
    try:
        raw = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicReleaseValidationError("release manifest is not valid JSON") from error
    if not isinstance(raw, Mapping):
        raise PublicReleaseValidationError("release manifest must be an object")
    document = cast(Mapping[str, object], raw)
    if set(document) != {
        "artifacts",
        "distribution",
        "qualified_commit",
        "schema",
        "tag_created",
        "tag_planned",
        "version",
    }:
        raise PublicReleaseValidationError("release manifest fields are invalid")
    if (
        document["distribution"] != "openrtl"
        or document["qualified_commit"] != QUALIFIED_COMMIT
        or document["schema"] != "openrtl-release-manifest.v1"
        or document["tag_created"] is not False
        or document["tag_planned"] != TAG
        or document["version"] != VERSION
    ):
        raise PublicReleaseValidationError("release manifest identity is invalid")
    raw_artifacts = document["artifacts"]
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 3:
        raise PublicReleaseValidationError("release artifact manifest is invalid")
    artifacts: list[PublishedArtifact] = []
    for index, raw_artifact in enumerate(raw_artifacts):
        if not isinstance(raw_artifact, Mapping):
            raise PublicReleaseValidationError("release artifact entry is invalid")
        artifact = cast(Mapping[str, object], raw_artifact)
        if set(artifact) != {"filename", "media_type", "sha256", "size_bytes"}:
            raise PublicReleaseValidationError("release artifact fields are invalid")
        filename = _text(artifact["filename"], f"artifact {index} filename")
        digest = _text(artifact["sha256"], f"artifact {index} sha256")
        if PurePosixPath(filename).name != filename or _DIGEST.fullmatch(digest) is None:
            raise PublicReleaseValidationError("release artifact identity is invalid")
        artifacts.append(
            PublishedArtifact(
                filename=filename,
                media_type=_text(artifact["media_type"], f"artifact {index} media type"),
                sha256=digest,
                size_bytes=_integer(artifact["size_bytes"], f"artifact {index} size"),
            )
        )
    expected = {
        "openrtl-0.2.0-py3-none-any.whl",
        "openrtl-0.2.0.tar.gz",
        "openrtl-examples-0.2.0.tar.gz",
    }
    if {artifact.filename for artifact in artifacts} != expected:
        raise PublicReleaseValidationError("release artifact filenames are invalid")
    return tuple(artifacts)


def _trusted_github_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "github.com" or host.endswith(".githubusercontent.com")
    )


def _download(url: str, target: Path, *, maximum_bytes: int) -> bytes:
    if not _trusted_github_url(url):
        raise PublicReleaseValidationError("download URL is not an allowed GitHub URL")
    if target.exists() or target.is_symlink():
        raise PublicReleaseValidationError(f"download target already exists: {target.name}")
    request = Request(url, headers={"User-Agent": "openrtl-public-release-acceptance/1"})
    response = cast(_ReadableResponse, urlopen(request, timeout=60))
    content = bytearray()
    with response:
        if not _trusted_github_url(response.geturl()):
            raise PublicReleaseValidationError("download redirected outside allowed GitHub hosts")
        while True:
            chunk = response.read(min(1024 * 1024, maximum_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise PublicReleaseValidationError("download exceeds its byte bound")
    target.write_bytes(content)
    return bytes(content)


def _verify_artifact(path: Path, artifact: PublishedArtifact) -> None:
    if not path.is_file() or path.is_symlink():
        raise PublicReleaseValidationError(f"release artifact is not a regular file: {artifact.filename}")
    content = path.read_bytes()
    if len(content) != artifact.size_bytes or _sha256(content) != artifact.sha256:
        raise PublicReleaseValidationError(f"release artifact bytes are invalid: {artifact.filename}")


def _archive_target(root: Path, member: tarfile.TarInfo, prefix: str) -> Path:
    if not member.isfile() or member.issym() or member.islnk():
        raise PublicReleaseValidationError("examples archive contains a non-regular member")
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2 or path.parts[0] != prefix:
        raise PublicReleaseValidationError("examples archive member escapes its root")
    relative = Path(*path.parts[1:])
    resolved_root = root.resolve(strict=True)
    target = resolved_root / relative
    if resolved_root not in target.resolve().parents:
        raise PublicReleaseValidationError("examples archive target escapes its root")
    return target


def extract_examples(archive_path: Path, output: Path) -> None:
    """Extract only regular, contained files from the companion archive."""
    if output.exists() or output.is_symlink():
        raise PublicReleaseValidationError("examples output already exists")
    output.mkdir(parents=True)
    resolved_output = output.resolve(strict=True)
    prefix = f"openrtl-examples-{VERSION}"
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            target = _archive_target(resolved_output, member, prefix)
            relative = target.relative_to(resolved_output).as_posix()
            if relative in seen:
                raise PublicReleaseValidationError("examples archive contains a duplicate member")
            seen.add(relative)
            source = archive.extractfile(member)
            if source is None:
                raise PublicReleaseValidationError("examples archive member is unreadable")
            content = source.read()
            if len(content) != member.size:
                raise PublicReleaseValidationError("examples archive member size changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise PublicReleaseValidationError("examples archive member collides")
            target.write_bytes(content)
    verifier = resolved_output / "tools/verify_release_install.py"
    if not verifier.is_file() or verifier.is_symlink():
        raise PublicReleaseValidationError("examples archive is missing its verifier")


def _run(arguments: Sequence[str], *, root: Path, environment: Mapping[str, str] | None = None) -> None:
    completed = subprocess.run(
        arguments,
        cwd=root,
        env=None if environment is None else dict(environment),
        check=False,
    )
    if completed.returncode != 0:
        raise PublicReleaseValidationError(f"public acceptance command failed: {arguments[0]}")


def _output(arguments: Sequence[str], *, root: Path) -> str:
    completed = subprocess.run(
        arguments,
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise PublicReleaseValidationError(f"public acceptance inspection failed: {arguments[0]}")
    return completed.stdout.strip()


def _prepare_agentrig(git: str, output: Path) -> Path:
    source = output / "agentrig-source"
    source.mkdir()
    _run((git, "init", "--quiet"), root=source)
    _run((git, "remote", "add", "origin", AGENTRIG_REPOSITORY), root=source)
    _run((git, "fetch", "--quiet", "--depth", "1", "origin", AGENTRIG_COMMIT), root=source)
    _run((git, "checkout", "--quiet", "--detach", "FETCH_HEAD"), root=source)
    if _output((git, "rev-parse", "HEAD"), root=source) != AGENTRIG_COMMIT:
        raise PublicReleaseValidationError("public AgentRig source commit is invalid")
    return source


def _installed_version(python: Path, distribution: str, root: Path) -> str:
    return _output(
        (
            str(python),
            "-c",
            "from importlib.metadata import version; print(version('" + distribution + "'))",
        ),
        root=root,
    )


def validate_public_release(output: Path, *, with_verilator: bool) -> Path:
    """Download, install, and exercise the immutable public release."""
    if output.exists() or output.is_symlink():
        raise PublicReleaseValidationError("acceptance output directory already exists")
    output.mkdir(parents=True)
    downloads = output / "downloads"
    downloads.mkdir()
    manifest_path = downloads / RELEASE_MANIFEST_NAME
    manifest_content = _download(
        f"{RELEASE_BASE_URL}/{RELEASE_MANIFEST_NAME}",
        manifest_path,
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    artifacts = parse_release_manifest(manifest_content)
    for artifact in artifacts:
        target = downloads / artifact.filename
        _download(
            f"{RELEASE_BASE_URL}/{artifact.filename}",
            target,
            maximum_bytes=min(_MAX_ARTIFACT_BYTES, artifact.size_bytes),
        )
        _verify_artifact(target, artifact)
    print("CHECKPOINT public_release_assets exact_downloaded_bytes")

    git = shutil.which("git")
    if git is None or not Path(git).is_file():
        raise PublicReleaseValidationError("git executable is unavailable")
    agentrig_source = _prepare_agentrig(git, output)
    print(f"CHECKPOINT public_agentrig_source exact_commit {AGENTRIG_COMMIT}")

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    venv = output / "venv"
    _run((sys.executable, "-m", "venv", str(venv)), root=output, environment=environment)
    python = venv / "bin/python"
    if not python.is_file():
        raise PublicReleaseValidationError("acceptance virtual environment is incomplete")
    _run((str(python), "-m", "pip", "install", str(agentrig_source)), root=output, environment=environment)
    if _installed_version(python, "agentrig", output) != AGENTRIG_VERSION:
        raise PublicReleaseValidationError("installed AgentRig version is invalid")
    wheel = downloads / f"openrtl-{VERSION}-py3-none-any.whl"
    _run((str(python), "-m", "pip", "install", "--no-deps", str(wheel)), root=output, environment=environment)
    if with_verilator:
        _run((str(python), "-m", "pip", "install", "cocotb==2.0.1"), root=output, environment=environment)
    if _installed_version(python, "openrtl", output) != VERSION:
        raise PublicReleaseValidationError("installed OpenRTL version is invalid")
    print("CHECKPOINT public_release_install exact_versions")

    examples = output / f"openrtl-examples-{VERSION}"
    extract_examples(downloads / f"openrtl-examples-{VERSION}.tar.gz", examples)
    verification = [
        str(python),
        "tools/verify_release_install.py",
        "--examples-root",
        str(examples),
    ]
    if with_verilator:
        verification.append("--with-verilator")
    run_environment = dict(environment)
    run_environment["PATH"] = f"{venv / 'bin'}:{run_environment.get('PATH', '')}"
    _run(tuple(verification), root=examples, environment=run_environment)
    print("CHECKPOINT public_release_examples passed")

    report = output / "public-release-acceptance.json"
    report.write_text(
        json.dumps(
            {
                "agentrig": {
                    "commit": AGENTRIG_COMMIT,
                    "repository": AGENTRIG_REPOSITORY,
                    "version": AGENTRIG_VERSION,
                },
                "artifacts": [artifact.to_dict() for artifact in artifacts],
                "manifest_sha256": RELEASE_MANIFEST_SHA256,
                "qualified_commit": QUALIFIED_COMMIT,
                "release": RELEASE_PAGE_URL,
                "schema": "openrtl-public-release-acceptance.v1",
                "status": "passed",
                "tag": TAG,
                "version": VERSION,
                "with_verilator": with_verilator,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"COLLATERAL public_release_acceptance={report}")
    print("OPENRTL_PUBLIC_RELEASE_ACCEPTANCE_STATUS=0")
    return report


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--with-verilator", action="store_true")
    parsed = parser.parse_args(arguments)
    output = parsed.output_directory.resolve()
    validate_public_release(output, with_verilator=bool(parsed.with_verilator))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
