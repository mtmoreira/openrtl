"""Qualify an OpenRTL release candidate from public dependency source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tomllib
from typing import Sequence

from tools.validate_release import ReleaseManifest, ReleaseValidationError, validate_release


CANDIDATE_SCHEMA = "openrtl-release-candidate-qualification.v1"
PUBLIC_AGENTRIG_REPOSITORY = "https://github.com/mtmoreira/agentrig.git"
PUBLIC_AGENTRIG_TAG = "v0.3.0"
PUBLIC_AGENTRIG_COMMIT = "31b2ecae0605f0d6b63b5f060c929ca567ae16f2"
PUBLIC_AGENTRIG_VERSION = "0.3.0"


def validate_candidate_metadata(root: Path, manifest: ReleaseManifest) -> None:
    with (root / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source).get("project")
    if (not isinstance(project, dict) or manifest.version not in {"0.3.0", "0.4.0"}
            or project.get("version") != manifest.version):
        raise ReleaseValidationError("candidate version must match OpenRTL 0.3.0 or 0.4.0 metadata")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or dependencies != ["agentrig==0.3.0"]:
        raise ReleaseValidationError("candidate must pin exactly agentrig==0.3.0")


def _git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("/usr/bin/git", "-C", str(source), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ReleaseValidationError("public AgentRig checkout identity is unreadable")
    return completed.stdout.strip()


def validate_public_agentrig_checkout(source: Path) -> None:
    resolved = source.resolve(strict=True)
    if _git(resolved, "remote", "get-url", "origin") != PUBLIC_AGENTRIG_REPOSITORY:
        raise ReleaseValidationError("AgentRig source is not the required public repository")
    if _git(resolved, "rev-parse", "HEAD") != PUBLIC_AGENTRIG_COMMIT:
        raise ReleaseValidationError("AgentRig source commit is not the qualified public commit")
    if _git(resolved, "rev-parse", f"{PUBLIC_AGENTRIG_TAG}^{{commit}}") != PUBLIC_AGENTRIG_COMMIT:
        raise ReleaseValidationError("AgentRig public tag does not resolve to the qualified commit")


def extract_examples(archive_path: Path, destination: Path, version: str) -> Path:
    """Extract the exact regular-file examples tree without traversal or links."""
    prefix = f"openrtl-examples-{version}"
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = tuple(archive.getmembers())
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not member.isfile()
                or member.issym()
                or member.islnk()
                or not relative.parts
                or relative.parts[0] != prefix
            ):
                raise ReleaseValidationError("examples archive contains an unsafe member")
        for member in members:
            relative = PurePosixPath(member.name)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ReleaseValidationError("examples archive member is unreadable")
            target.write_bytes(stream.read())
    return destination / prefix


def _artifact_documents(manifest: ReleaseManifest) -> list[dict[str, str | int]]:
    return [artifact.to_dict() for artifact in manifest.artifacts]


def candidate_python_executable(candidate_python: Path) -> Path:
    """Keep the virtualenv entry path; resolving its symlink loses the venv."""
    executable = candidate_python.absolute()
    if not executable.is_file():
        raise ReleaseValidationError("candidate Python executable is unavailable")
    return executable


def candidate_environment(executable: Path) -> dict[str, str]:
    """Expose only the candidate venv's console scripts ahead of host tools."""
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    inherited_path = environment.get("PATH", "")
    environment["PATH"] = f"{executable.parent}{os.pathsep}{inherited_path}"
    return environment


def qualify_candidate(
    root: Path,
    dist: Path,
    *,
    commit: str,
    agentrig_source: Path,
    candidate_python: Path,
    output_directory: Path,
    with_verilator: bool,
) -> Path:
    manifest = validate_release(root, dist, commit=commit)
    validate_candidate_metadata(root, manifest)
    validate_public_agentrig_checkout(agentrig_source)
    manifest_path = dist / manifest.filename
    if not manifest_path.is_file() or manifest_path.read_text(encoding="utf-8") != manifest.to_json():
        raise ReleaseValidationError("candidate manifest is missing or differs from qualified artifacts")
    examples = dist / f"openrtl-examples-{manifest.version}.tar.gz"
    extracted = extract_examples(examples, output_directory / "examples", manifest.version)
    verifier = extracted / "tools/verify_release_install.py"
    executable = candidate_python_executable(candidate_python)
    command = [
        str(executable),
        str(verifier),
        "--examples-root",
        str(extracted),
        "--expected-version",
        manifest.version,
        "--expected-agentrig-version",
        PUBLIC_AGENTRIG_VERSION,
    ]
    if with_verilator:
        command.append("--with-verilator")
    completed = subprocess.run(
        command,
        cwd=extracted,
        check=False,
        env=candidate_environment(executable),
    )
    if completed.returncode != 0:
        raise ReleaseValidationError("isolated installed release verification failed")
    comparison_path = extracted / "build/release-fifo-repair/comparison.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if with_verilator else None
    visual = comparison.get("visual_evidence") if isinstance(comparison, dict) else None
    if with_verilator and (not isinstance(visual, dict) or visual.get("status") != "visibly_distinct"):
        raise ReleaseValidationError("candidate lacks visibly distinct repair waveform evidence")
    output = output_directory / "qualification.json"
    extended = extracted / "build/release-v040/acceptance.json"
    if manifest.version == "0.4.0" and with_verilator:
        extended_report = json.loads(extended.read_text(encoding="utf-8"))
        if extended_report.get("status") != "passed" or extended_report.get("case_count") != 3:
            raise ReleaseValidationError("candidate lacks complete installed 0.4 example evidence")
    document = {
        "agentrig": {
            "commit": PUBLIC_AGENTRIG_COMMIT,
            "repository": PUBLIC_AGENTRIG_REPOSITORY,
            "tag": PUBLIC_AGENTRIG_TAG,
            "version": PUBLIC_AGENTRIG_VERSION,
        },
        "artifacts": _artifact_documents(manifest),
        "qualified_commit": commit,
        "release_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "schema": CANDIDATE_SCHEMA,
        "status": "qualified_local_candidate",
        "tag_created": False,
        "version": manifest.version,
        "verilator_repair": "visibly_distinct" if with_verilator else "not_selected",
        "extended_examples_sha256": (
            hashlib.sha256(extended.read_bytes()).hexdigest()
            if manifest.version == "0.4.0" and with_verilator else None
        ),
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=root)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--agentrig-source", type=Path, required=True)
    parser.add_argument("--candidate-python", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--with-verilator", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        output = qualify_candidate(
            arguments.repository_root,
            arguments.dist_dir,
            commit=arguments.commit,
            agentrig_source=arguments.agentrig_source,
            candidate_python=arguments.candidate_python,
            output_directory=arguments.output_directory,
            with_verilator=arguments.with_verilator,
        )
    except (OSError, ReleaseValidationError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"release candidate qualification failed: {error}")
        return 1
    print(f"CHECKPOINT release_candidate qualified {output}")
    print(f"CHECKPOINT public_agentrig exact {PUBLIC_AGENTRIG_TAG} {PUBLIC_AGENTRIG_COMMIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
