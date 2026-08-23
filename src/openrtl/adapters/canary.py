"""Fail-closed ingestion of hash-bound Verilator FIFO canary evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, cast
import xml.etree.ElementTree as ET

from openrtl.adapters.logs import LogEvent, parse_jsonl_events
from openrtl.adapters.waveforms import VcdIndex, WaveformFocus
from openrtl.domain import (
    ArtifactRef,
    EvidenceRecord,
    LogAnchor,
    RequirementAnchor,
    RunBundle,
    RunStatus,
    SourceAnchor,
    VerifiedRunEvidence,
    WaveformAnchor,
)
from openrtl.domain._validation import identifier, relative_path


_SCHEMA = "openrtl.verilator-canary-evidence.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = {
    "log": 8 * 1024 * 1024,
    "results": 1024 * 1024,
    "waveform": 64 * 1024 * 1024,
    "rtl": 1024 * 1024,
}
_FOCUS_SIGNALS = (
    "sync_fifo.wr_valid",
    "sync_fifo.rd_valid",
    "sync_fifo.level",
)
_FIFO_REQUIREMENTS = (
    "fifo.reset",
    "fifo.write",
    "fifo.read",
    "fifo.order",
    "fifo.backpressure",
    "fifo.simultaneous",
    "fifo.wrap",
    "fifo.status",
)


def load_fifo_canary_evidence(
    root: Path,
    manifest_path: Path,
    artifact_refs: tuple[ArtifactRef, ...],
) -> VerifiedRunEvidence:
    """Verify retained collateral and normalize it into domain evidence."""

    resolved_root = root.resolve(strict=True)
    if not artifact_refs or len(set(artifact_refs)) != len(artifact_refs):
        raise ValueError("artifact_refs must be non-empty and unique")
    manifest = _bounded_file(resolved_root, manifest_path, _MAX_MANIFEST_BYTES, "manifest")
    try:
        value = json.loads(manifest.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("canary evidence manifest is invalid JSON") from error
    payload = _object(value, "manifest")
    _exact_keys(
        payload,
        {
            "artifacts",
            "requirements",
            "rtl",
            "run_id",
            "schema",
            "seed",
            "status",
            "testcase",
            "tool_profile_id",
            "top",
        },
        "manifest",
    )
    if payload["schema"] != _SCHEMA or payload["status"] != "passed":
        raise ValueError("canary evidence manifest is not a passing supported schema")
    if (
        payload["run_id"] != "fifo.verilator.canary"
        or payload["tool_profile_id"] != "verilator.cocotb"
        or payload["top"] != "sync_fifo"
        or payload["testcase"] != "test_sync_fifo.randomized_fifo_scoreboard"
    ):
        raise ValueError("canary evidence manifest does not describe the FIFO canary")
    run_id = identifier(_string(payload["run_id"], "run_id"), "run_id")
    tool_profile_id = identifier(
        _string(payload["tool_profile_id"], "tool_profile_id"),
        "tool_profile_id",
    )
    seed = payload["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("canary evidence seed is invalid")
    requirements_value = payload["requirements"]
    if not isinstance(requirements_value, list):
        raise ValueError("canary evidence requirements must be a list")
    requirements = tuple(identifier(_string(item, "requirement"), "requirement") for item in requirements_value)
    if requirements != _FIFO_REQUIREMENTS:
        raise ValueError("canary evidence requirements do not match the FIFO contract")

    rtl = _verified_record(resolved_root, payload["rtl"], "rtl")
    if rtl.relative_path != "examples/fifo/rtl/sync_fifo.sv":
        raise ValueError("canary evidence RTL path is not the FIFO implementation")
    artifacts_value = _object(payload["artifacts"], "artifacts")
    _exact_keys(artifacts_value, {"log", "results", "waveform"}, "artifacts")
    log = _verified_record(resolved_root, artifacts_value["log"], "log")
    results = _verified_record(resolved_root, artifacts_value["results"], "results")
    waveform = _verified_record(resolved_root, artifacts_value["waveform"], "waveform")
    _verify_results(results.content)
    events = _scoreboard_events(log.content)
    _verify_transfer_events(events)
    focus = _waveform_focus(waveform)

    evidence_id = "ev.fifo.verilator.canary"
    evidence = EvidenceRecord(
        evidence_id,
        "Passing FIFO scoreboard evidence verified from hash-bound Verilator collateral.",
        (
            *(RequirementAnchor(item) for item in requirements),
            SourceAnchor(
                rtl.relative_path,
                1,
                len(rtl.content.decode("utf-8").splitlines()),
                f"sha256:{rtl.sha256}",
            ),
            LogAnchor(run_id, "transfer.accepted"),
            WaveformAnchor(
                f"{run_id}.trace",
                focus.start_fs,
                focus.end_fs,
                focus.signals,
                focus.markers_fs,
            ),
        ),
        artifact_refs,
    )
    run = RunBundle(
        run_id,
        RunStatus.PASSED,
        tool_profile_id,
        seed,
        artifact_refs,
        (evidence_id,),
        log.relative_path,
        waveform.relative_path,
    )
    return VerifiedRunEvidence(
        manifest.relative_path,
        f"sha256:{manifest.sha256}",
        evidence,
        run,
    )


@dataclass(frozen=True)
class _VerifiedFile:
    relative_path: str
    content: bytes
    sha256: str


def _bounded_file(root: Path, path: Path, maximum: int, label: str) -> _VerifiedFile:
    candidate = path if path.is_absolute() else root / path
    try:
        lexical_relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"canary evidence {label} is outside the repository") from error
    if ".." in lexical_relative.parts:
        raise ValueError(f"canary evidence {label} path is invalid")
    current = root
    for part in lexical_relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"canary evidence {label} path contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"canary evidence {label} is missing") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"canary evidence {label} is outside the repository")
    relative = resolved.relative_to(root)
    content = resolved.read_bytes()
    if not content or len(content) > maximum:
        raise ValueError(f"canary evidence {label} size is invalid")
    return _VerifiedFile(relative.as_posix(), content, hashlib.sha256(content).hexdigest())


def _verified_record(root: Path, value: object, label: str) -> _VerifiedFile:
    record = _object(value, label)
    _exact_keys(record, {"path", "sha256", "size_bytes"}, label)
    record_path = relative_path(_string(record["path"], f"{label} path"), f"{label} path")
    expected_sha256 = _string(record["sha256"], f"{label} sha256")
    expected_size = record["size_bytes"]
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError(f"canary evidence {label} sha256 is invalid")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 1:
        raise ValueError(f"canary evidence {label} size is invalid")
    verified = _bounded_file(root, Path(record_path), _MAX_ARTIFACT_BYTES[label], label)
    if verified.sha256 != expected_sha256 or len(verified.content) != expected_size:
        raise ValueError(f"canary evidence {label} digest or size mismatch")
    return verified


def _verify_results(content: bytes) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("canary evidence results XML is invalid") from error
    if len(list(root.iter("testcase"))) != 1 or list(root.iter("failure")) or list(root.iter("error")):
        raise ValueError("canary evidence results do not contain one passing test")


def _scoreboard_events(content: bytes) -> tuple[LogEvent, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("canary evidence log is not UTF-8") from error
    marker = '{"component":"fifo.scoreboard"'
    events: list[LogEvent] = []
    for line in lines:
        offset = line.find(marker)
        if offset >= 0:
            events.extend(parse_jsonl_events(line[offset:]))
    if not events:
        raise ValueError("canary evidence log has no scoreboard events")
    return tuple(events)


def _verify_transfer_events(events: tuple[LogEvent, ...]) -> None:
    transfers = tuple(event for event in events if event.event == "transfer.accepted")
    if not transfers or any("fifo.order" not in event.requirement_ids for event in transfers):
        raise ValueError("canary evidence log has invalid transfer events")
    fields = tuple(dict(event.fields or {}) for event in transfers)
    if not any(item.get("write") is True for item in fields):
        raise ValueError("canary evidence log has no accepted write")
    if not any(item.get("read") is True for item in fields):
        raise ValueError("canary evidence log has no accepted read")
    if not any(item.get("write") is True and item.get("read") is True for item in fields):
        raise ValueError("canary evidence log has no simultaneous transfer")


def _waveform_focus(waveform: _VerifiedFile) -> WaveformFocus:
    try:
        index = VcdIndex.parse(waveform.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("canary evidence waveform is invalid") from error
    timestamps = tuple(
        transition.timestamp_fs
        for signal in _FOCUS_SIGNALS
        for transition in index.transitions(signal)
    )
    if not timestamps or max(timestamps) <= 0:
        raise ValueError("canary evidence waveform has no focused transitions")
    return index.focus(waveform.relative_path, _FOCUS_SIGNALS, 0, max(timestamps))


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"canary evidence {label} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"canary evidence {label} must be a string")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"canary evidence {label} fields are invalid")
