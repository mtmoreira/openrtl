"""Validate published OpenRTL 0.3.0 from public inputs only."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_public_release import (
    PublishedArtifact,
    PublicReleaseValidationError,
    _download,
    _output,
    _run,
    _sha256,
    _verify_artifact,
)
from tools.validate_release_candidate import extract_examples


VERSION = "0.3.0"
TAG = "v0.3.0"
QUALIFIED_COMMIT = "6eedc375db42b99ea5ce38f150ead92599b259fd"
RELEASE_COMMIT = "a69d27d645d351ade3a8974acf21c21b31c8dc5e"
OPENRTL_REPOSITORY = "https://github.com/mtmoreira/openrtl.git"
RELEASE_BASE_URL = "https://github.com/mtmoreira/openrtl/releases/download/v0.3.0"
RELEASE_PAGE_URL = "https://github.com/mtmoreira/openrtl/releases/tag/v0.3.0"
RELEASE_MANIFEST_NAME = "openrtl-0.3.0-release.json"
RELEASE_MANIFEST_SHA256 = "f0302aa3cba40282fac3b53536392b76dc279ae50837a2ceeb82d981cf3cede6"
AGENTRIG_REPOSITORY = "https://github.com/mtmoreira/agentrig.git"
AGENTRIG_TAG = "v0.3.0"
AGENTRIG_COMMIT = "31b2ecae0605f0d6b63b5f060c929ca567ae16f2"
AGENTRIG_VERSION = "0.3.0"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublicReleaseValidationError(f"{label} is invalid")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PublicReleaseValidationError(f"{label} is invalid")
    return value


def parse_release_manifest(content: bytes) -> tuple[PublishedArtifact, ...]:
    """Parse the exact immutable v0.3.0 manifest."""
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
        "openrtl-0.3.0-py3-none-any.whl",
        "openrtl-0.3.0.tar.gz",
        "openrtl-examples-0.3.0.tar.gz",
    }
    if {artifact.filename for artifact in artifacts} != expected:
        raise PublicReleaseValidationError("release artifact filenames are invalid")
    return tuple(artifacts)


def _prepare_tagged_source(
    git: str,
    output: Path,
    *,
    directory_name: str,
    repository: str,
    tag: str,
    commit: str,
) -> Path:
    source = output / directory_name
    source.mkdir()
    _run((git, "init", "--quiet"), root=source)
    _run((git, "remote", "add", "origin", repository), root=source)
    _run(
        (git, "fetch", "--quiet", "--depth", "1", "origin", f"refs/tags/{tag}:refs/tags/{tag}"),
        root=source,
    )
    if _output((git, "remote", "get-url", "origin"), root=source) != repository:
        raise PublicReleaseValidationError("public source repository identity is invalid")
    if _output((git, "cat-file", "-t", tag), root=source) != "tag":
        raise PublicReleaseValidationError("public source tag is not annotated")
    if _output((git, "rev-parse", f"{tag}^{{commit}}"), root=source) != commit:
        raise PublicReleaseValidationError("public source tag commit is invalid")
    _run((git, "checkout", "--quiet", "--detach", commit), root=source)
    if _output((git, "rev-parse", "HEAD"), root=source) != commit:
        raise PublicReleaseValidationError("public source checkout commit is invalid")
    return source


def verification_command(python: Path, examples: Path, *, with_verilator: bool) -> tuple[str, ...]:
    command = [
        str(python),
        "tools/verify_release_install.py",
        "--examples-root",
        str(examples),
        "--expected-version",
        VERSION,
        "--expected-agentrig-version",
        AGENTRIG_VERSION,
    ]
    if with_verilator:
        command.append("--with-verilator")
    return tuple(command)


def validate_public_release(output: Path, *, with_verilator: bool) -> Path:
    """Download, install, and exercise immutable OpenRTL 0.3.0."""
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
    print("CHECKPOINT public_release_v030_assets exact_downloaded_bytes")

    git = shutil.which("git")
    if git is None or not Path(git).is_file():
        raise PublicReleaseValidationError("git executable is unavailable")
    _prepare_tagged_source(
        git,
        output,
        directory_name="openrtl-release-source",
        repository=OPENRTL_REPOSITORY,
        tag=TAG,
        commit=RELEASE_COMMIT,
    )
    agentrig_source = _prepare_tagged_source(
        git,
        output,
        directory_name="agentrig-release-source",
        repository=AGENTRIG_REPOSITORY,
        tag=AGENTRIG_TAG,
        commit=AGENTRIG_COMMIT,
    )
    print("CHECKPOINT public_release_v030_tags exact_annotated_commits")

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    venv = output / "venv"
    _run((sys.executable, "-m", "venv", str(venv)), root=output, environment=environment)
    python = venv / "bin/python"
    if not python.is_file():
        raise PublicReleaseValidationError("acceptance virtual environment is incomplete")
    _run((str(python), "-m", "pip", "install", str(agentrig_source)), root=output, environment=environment)
    if with_verilator:
        _run((str(python), "-m", "pip", "install", "cocotb==2.0.1"), root=output, environment=environment)
    wheel = downloads / f"openrtl-{VERSION}-py3-none-any.whl"
    _run((str(python), "-m", "pip", "install", "--no-deps", str(wheel)), root=output, environment=environment)
    if _output((str(python), "-c", "from importlib.metadata import version; print(version('agentrig'))"), root=output) != AGENTRIG_VERSION:
        raise PublicReleaseValidationError("installed AgentRig version is invalid")
    if _output((str(python), "-c", "from importlib.metadata import version; print(version('openrtl'))"), root=output) != VERSION:
        raise PublicReleaseValidationError("installed OpenRTL version is invalid")
    print("CHECKPOINT public_release_v030_install exact_versions")

    examples = extract_examples(
        downloads / f"openrtl-examples-{VERSION}.tar.gz",
        output / "examples",
        VERSION,
    )
    run_environment = dict(environment)
    run_environment["PATH"] = f"{venv / 'bin'}{os.pathsep}{run_environment.get('PATH', '')}"
    _run(
        verification_command(python, examples, with_verilator=with_verilator),
        root=examples,
        environment=run_environment,
    )
    print("CHECKPOINT public_release_v030_examples passed")

    report = output / "public-release-v0.3.0-acceptance.json"
    report.write_text(
        json.dumps(
            {
                "agentrig": {
                    "commit": AGENTRIG_COMMIT,
                    "repository": AGENTRIG_REPOSITORY,
                    "tag": AGENTRIG_TAG,
                    "version": AGENTRIG_VERSION,
                },
                "artifacts": [artifact.to_dict() for artifact in artifacts],
                "manifest_sha256": RELEASE_MANIFEST_SHA256,
                "qualified_commit": QUALIFIED_COMMIT,
                "release": RELEASE_PAGE_URL,
                "release_commit": RELEASE_COMMIT,
                "schema": "openrtl-public-release-acceptance.v2",
                "status": "passed",
                "tag": TAG,
                "version": VERSION,
                "verilator_repair": "visibly_distinct" if with_verilator else "not_selected",
                "with_verilator": with_verilator,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"COLLATERAL public_release_v030_acceptance={report}")
    print("OPENRTL_PUBLIC_RELEASE_V030_ACCEPTANCE_STATUS=0")
    return report


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--with-verilator", action="store_true")
    parsed = parser.parse_args(arguments)
    validate_public_release(parsed.output_directory.resolve(), with_verilator=bool(parsed.with_verilator))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
