"""Profile-driven ingestion of passing, hash-bound simulation evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, cast
import xml.etree.ElementTree as ET

from openrtl.adapters.catalog import LocalDesignCatalog
from openrtl.adapters.waveforms import VcdIndex
from openrtl.application.package_candidates import (
    SimulationProfileFile,
    VerifiedPackageCandidate,
    VerifiedSimulationProfile,
)
from openrtl.application.reviews import build_requirement_coverage
from openrtl.domain import (
    ArtifactKind,
    ArtifactRef,
    DesignPackage,
    EvidenceRecord,
    InterfacePort,
    PackageFile,
    Parameter,
    PortDirection,
    RequirementAnchor,
    RunBundle,
    RunStatus,
    SourceAnchor,
    TrustLevel,
    VerifiedRunEvidence,
    WaveformAnchor,
)
from openrtl.domain._validation import identifier, relative_path


_PROFILE_SCHEMA = "openrtl.verified-simulation-profile.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 1024 * 1024
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def load_verified_simulation_profile(root: Path, profile_path: Path) -> VerifiedSimulationProfile:
    resolved_root = root.resolve(strict=True)
    profile_file = _bounded_file(resolved_root, profile_path, _MAX_JSON_BYTES, "profile")
    payload = _json_object(profile_file.content, "profile")
    _exact_keys(
        payload,
        {
            "artifacts", "design_id", "evidence_id", "files", "focus_signals",
            "license_id", "package_id", "package_version", "parameters", "ports",
            "profile_id", "requirements", "run", "schema",
        },
        "profile",
    )
    if payload["schema"] != _PROFILE_SCHEMA:
        raise ValueError("verified simulation profile schema is unsupported")
    files = tuple(_profile_file(value) for value in _list(payload["files"], "files"))
    if not files or len({value.path for value in files}) != len(files):
        raise ValueError("verified simulation profile files must be non-empty and unique")
    run = _object(payload["run"], "run")
    _exact_keys(run, {"id", "manifest_schema", "seed", "testcase", "tool_profile_id", "top"}, "run")
    artifacts = _object(payload["artifacts"], "artifacts")
    _exact_keys(artifacts, {"log", "results", "source", "waveform"}, "artifacts")
    requirements = tuple(identifier(_string(value, "requirement"), "requirement") for value in _list(payload["requirements"], "requirements"))
    focus_signals = tuple(_string(value, "focus signal") for value in _list(payload["focus_signals"], "focus_signals"))
    if not requirements or len(set(requirements)) != len(requirements):
        raise ValueError("verified simulation profile requirements must be non-empty and unique")
    if not focus_signals or len(set(focus_signals)) != len(focus_signals):
        raise ValueError("verified simulation profile focus signals must be non-empty and unique")
    seed = run["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("verified simulation profile seed is invalid")
    return VerifiedSimulationProfile(
        profile_id=identifier(_string(payload["profile_id"], "profile_id"), "profile_id"),
        profile_uri=profile_file.path,
        profile_digest=f"sha256:{profile_file.sha256}",
        manifest_schema=_string(run["manifest_schema"], "manifest_schema"),
        design_id=identifier(_string(payload["design_id"], "design_id"), "design_id"),
        package_id=identifier(_string(payload["package_id"], "package_id"), "package_id"),
        package_version=_string(payload["package_version"], "package_version"),
        license_id=_string(payload["license_id"], "license_id"),
        run_id=identifier(_string(run["id"], "run id"), "run id"),
        tool_profile_id=identifier(_string(run["tool_profile_id"], "tool_profile_id"), "tool_profile_id"),
        testcase=_string(run["testcase"], "testcase"),
        top=identifier(_string(run["top"], "top"), "top"),
        seed=seed,
        requirements=requirements,
        files=files,
        run_ref=ArtifactRef(f"{_string(payload['design_id'], 'design_id')}.run", 1),
        source_record_key=identifier(_string(artifacts["source"], "source key"), "source key"),
        log_artifact_key=identifier(_string(artifacts["log"], "log key"), "log key"),
        results_artifact_key=identifier(_string(artifacts["results"], "results key"), "results key"),
        waveform_artifact_key=identifier(_string(artifacts["waveform"], "waveform key"), "waveform key"),
        focus_signals=focus_signals,
        evidence_id=identifier(_string(payload["evidence_id"], "evidence_id"), "evidence_id"),
        ports=tuple(_port(value) for value in _list(payload["ports"], "ports")),
        parameters=tuple(_parameter(value) for value in _list(payload["parameters"], "parameters")),
    )


def load_verified_simulation_evidence(
    root: Path,
    profile: VerifiedSimulationProfile,
    manifest_path: Path,
) -> VerifiedRunEvidence:
    resolved_root = root.resolve(strict=True)
    manifest = _bounded_file(resolved_root, manifest_path, _MAX_JSON_BYTES, "manifest")
    payload = _json_object(manifest.content, "manifest")
    if payload.get("schema") != profile.manifest_schema or payload.get("status") != "passed":
        raise ValueError("simulation evidence does not match the passing profile schema")
    if profile.manifest_schema == "openrtl.verilator-canary-evidence.v1":
        _exact_keys(
            payload,
            {"artifacts", "requirements", "rtl", "run_id", "schema", "seed", "status", "testcase", "tool_profile_id", "top"},
            "FIFO manifest",
        )
        if (
            payload["run_id"] != profile.run_id
            or payload["tool_profile_id"] != profile.tool_profile_id
            or payload["testcase"] != profile.testcase
            or payload["top"] != profile.top
            or payload["seed"] != profile.seed
        ):
            raise ValueError("simulation evidence run identity does not match the profile")
        _exact_keys(_object(payload["artifacts"], "manifest artifacts"), {"log", "results", "waveform"}, "FIFO artifacts")
    elif profile.manifest_schema == "openrtl.skid-buffer-evidence.v1":
        _exact_keys(payload, {"artifacts", "production_rtl", "requirements", "schema", "status"}, "skid manifest")
        _exact_keys(
            _object(payload["artifacts"], "manifest artifacts"),
            {"before_focus", "before_log", "before_results", "before_waveform", "comparison", "debug_session", "repair_proposal", "repaired_focus", "repaired_log", "repaired_results", "repaired_waveform"},
            "skid artifacts",
        )
    else:
        raise ValueError("simulation evidence profile manifest schema is unsupported")
    requirements = payload.get("requirements")
    if requirements != list(profile.requirements):
        raise ValueError("simulation evidence requirements do not match the profile")
    source = _verified_record(resolved_root, payload.get(profile.source_record_key), "source")
    rtl_files = tuple(value for value in profile.files if value.kind is ArtifactKind.RTL)
    if len(rtl_files) != 1 or source.path != rtl_files[0].path:
        raise ValueError("simulation evidence source does not match the profile")
    artifacts = _object(payload.get("artifacts"), "manifest artifacts")
    log = _verified_record(resolved_root, artifacts.get(profile.log_artifact_key), "log")
    results = _verified_record(resolved_root, artifacts.get(profile.results_artifact_key), "results")
    waveform = _verified_record(resolved_root, artifacts.get(profile.waveform_artifact_key), "waveform")
    _verify_results(results.content, profile.testcase)
    if not log.content.strip():
        raise ValueError("simulation evidence log is empty")
    try:
        index = VcdIndex.parse(waveform.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("simulation evidence waveform is invalid") from error
    timestamps = tuple(
        transition.timestamp_fs
        for signal in profile.focus_signals
        for transition in index.transitions(signal)
    )
    if not timestamps or max(timestamps) <= 0:
        raise ValueError("simulation evidence waveform has no profile transitions")
    artifact_refs = profile.artifact_refs
    evidence = EvidenceRecord(
        profile.evidence_id,
        f"Passing {profile.design_id} simulation evidence verified from profile-bound collateral.",
        (
            *(RequirementAnchor(value) for value in profile.requirements),
            SourceAnchor(source.path, 1, len(source.content.decode("utf-8").splitlines()), f"sha256:{source.sha256}"),
            WaveformAnchor(f"{profile.run_id}.trace", 0, max(timestamps), profile.focus_signals),
        ),
        artifact_refs,
    )
    run = RunBundle(
        profile.run_id,
        RunStatus.PASSED,
        profile.tool_profile_id,
        profile.seed,
        artifact_refs,
        (profile.evidence_id,),
        log.path,
        waveform.path,
    )
    return VerifiedRunEvidence(manifest.path, f"sha256:{manifest.sha256}", evidence, run)


def build_verified_package_candidate(
    root: Path,
    profile: VerifiedSimulationProfile,
    verified_run: VerifiedRunEvidence,
    catalog: LocalDesignCatalog | None = None,
) -> VerifiedPackageCandidate:
    resolved_root = root.resolve(strict=True)
    if set(verified_run.run.artifact_refs) != set(profile.artifact_refs):
        raise ValueError("verified run artifact lineage does not match the profile")
    anchored = {value.requirement_id for value in verified_run.evidence.anchors if isinstance(value, RequirementAnchor)}
    if anchored != set(profile.requirements):
        raise ValueError("verified run requirement coverage does not match the profile")
    package_files: list[PackageFile] = []
    for value in profile.files:
        source = _bounded_file(resolved_root, Path(value.path), _MAX_ARTIFACT_BYTES, "package file")
        package_files.append(PackageFile(value.path, value.kind.value, f"sha256:{source.sha256}"))
    package = DesignPackage(
        profile.package_id,
        profile.package_version,
        profile.design_id,
        profile.license_id,
        TrustLevel.SIMULATION_VERIFIED,
        profile.ports,
        profile.parameters,
        tuple(package_files),
        (verified_run.evidence.evidence_id,),
    )
    catalog_manifest: str | None = None
    if catalog is not None:
        catalog_manifest = catalog.store_manifest(
            package,
            (
                ("simulation-profile", profile.profile_uri, profile.profile_digest),
                ("simulation-evidence", verified_run.artifact_uri, verified_run.content_digest),
            ),
        ).as_posix()
    return VerifiedPackageCandidate(
        profile,
        verified_run,
        package,
        build_requirement_coverage(profile.requirements, (verified_run.evidence,)),
        catalog_manifest,
    )


class _VerifiedFile:
    def __init__(self, path: str, content: bytes) -> None:
        self.path = path
        self.content = content
        self.sha256 = hashlib.sha256(content).hexdigest()


def _bounded_file(root: Path, path: Path, maximum: int, label: str) -> _VerifiedFile:
    candidate = path if path.is_absolute() else root / path
    try:
        lexical = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"verified simulation {label} is outside the repository") from error
    if ".." in lexical.parts:
        raise ValueError(f"verified simulation {label} path is invalid")
    current = root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"verified simulation {label} path contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"verified simulation {label} is missing") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"verified simulation {label} is outside the repository")
    content = resolved.read_bytes()
    if not content or len(content) > maximum:
        raise ValueError(f"verified simulation {label} size is invalid")
    return _VerifiedFile(resolved.relative_to(root).as_posix(), content)


def _verified_record(root: Path, value: object, label: str) -> _VerifiedFile:
    record = _object(value, label)
    _exact_keys(record, {"path", "sha256", "size_bytes"}, label)
    path = relative_path(_string(record["path"], f"{label} path"), f"{label} path")
    expected_digest = _string(record["sha256"], f"{label} sha256")
    expected_size = record["size_bytes"]
    if not _SHA256.fullmatch(expected_digest) or not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 1:
        raise ValueError(f"simulation evidence {label} record is invalid")
    verified = _bounded_file(root, Path(path), _MAX_ARTIFACT_BYTES, label)
    if verified.sha256 != expected_digest or len(verified.content) != expected_size:
        raise ValueError(f"simulation evidence {label} digest or size mismatch")
    return verified


def _verify_results(content: bytes, testcase: str) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("simulation evidence results XML is invalid") from error
    cases = list(root.iter("testcase"))
    expected_name = testcase.rsplit(".", 1)[-1]
    if len(cases) != 1 or cases[0].get("name") != expected_name or list(root.iter("failure")) or list(root.iter("error")):
        raise ValueError("simulation evidence results do not match the passing testcase")


def _profile_file(value: object) -> SimulationProfileFile:
    item = _object(value, "file")
    _exact_keys(item, {"artifact_id", "kind", "path"}, "file")
    try:
        kind = ArtifactKind(_string(item["kind"], "file kind"))
    except ValueError as error:
        raise ValueError("verified simulation profile file kind is invalid") from error
    return SimulationProfileFile(
        ArtifactRef(identifier(_string(item["artifact_id"], "artifact_id"), "artifact_id"), 1),
        kind,
        relative_path(_string(item["path"], "file path"), "file path"),
    )


def _port(value: object) -> InterfacePort:
    item = _object(value, "port")
    _exact_keys(item, {"direction", "name", "width"}, "port")
    width = item["width"]
    if not isinstance(width, int) or isinstance(width, bool):
        raise ValueError("verified simulation profile port width is invalid")
    return InterfacePort(_string(item["name"], "port name"), PortDirection(_string(item["direction"], "port direction")), width)


def _parameter(value: object) -> Parameter:
    item = _object(value, "parameter")
    _exact_keys(item, {"default", "maximum", "minimum", "name"}, "parameter")
    default = item["default"]
    if not isinstance(default, (int, bool, str)):
        raise ValueError("verified simulation profile parameter default is invalid")
    minimum = item["minimum"]
    maximum = item["maximum"]
    if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool)):
        raise ValueError("verified simulation profile parameter minimum is invalid")
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool)):
        raise ValueError("verified simulation profile parameter maximum is invalid")
    return Parameter(
        _string(item["name"], "parameter name"),
        default,
        minimum,
        maximum,
    )


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"verified simulation {label} is invalid JSON") from error


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"verified simulation {label} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"verified simulation {label} must be a list")
    return cast(list[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"verified simulation {label} must be a string")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"verified simulation {label} fields are invalid")
