"""Explicit source/evidence export to a new directory, with no session authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable

from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.domain.design_session import JsonObject, canonical, content_digest, require


def _read(path: Path, bound: int) -> bytes:
    selected = safe_root(path)
    fd = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= bound, "export_evidence_unavailable")
        data = stream.read(bound + 1)
    require(len(data) == info.st_size, "export_evidence_changed")
    return data


def export_material(store: DesignSessionStore, *, include_evidence: bool = True) -> tuple[JsonObject, dict[str, bytes]]:
    state = store.read()
    require(state["active"] is None, "export_requires_reconciled_session")
    files = {p: c.encode() for p, c in store.contents(state).items()}
    files.update({"imports/" + p: c.encode() for p, c in store.import_contents(state).items()})
    require(bool(files), "export_sources_missing")
    for key in ("spec", "manifest", "review"):
        if state[key] is not None:
            files["openrtl/" + key + ".json"] = canonical(state[key]) + b"\n"
    if state["simulation"] is not None and include_evidence:
        store.measurement(state)  # All retained runtime/run/artifact bindings, before any output.
        report = state["simulation"]
        run_id = report["run_id"]
        paths = ["runs/" + run_id + "/" + p for p in ("intent.json", "control/run.py", "evidence/report.json")]
        paths += [row["path"] for row in report["artifacts"].values()]
        for relative in paths:
            files[relative] = _read(store.root / relative, 16 * 1024 * 1024)
        # Check copied bytes as well as the earlier measurement, closing a read-between-checks gap.
        require(json.loads(files["runs/" + run_id + "/evidence/report.json"]) == report, "export_report_changed")
        for row in report["artifacts"].values():
            data = files[row["path"]]
            require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"], "export_evidence_changed")
        runner = files["runs/" + run_id + "/control/run.py"]
        require("sha256:" + hashlib.sha256(runner).hexdigest() == report["runtime"]["runner_digest"], "export_runner_changed")
        intent = json.loads(files["runs/" + run_id + "/intent.json"])
        require(intent["input_digest"] == report["input_digest"] and intent["profile_digest"] == report["runtime"]["profile_digest"]
                and intent["operation_id"] == run_id and intent["container_name"] == "openrtl-design-" + run_id,
                "export_intent_changed")
    files["OPENRTL-README.md"] = (
        "# OpenRTL source and evidence export\n\n"
        "rtl/, model/, dv/ and docs/ contain current design artifacts when present.\n"
        "imports/ contains the original immutable references; do not compile both copies.\n"
        "openrtl/spec.json is the reviewed requirements record; openrtl/manifest.json,\n"
        "when present, lists simulation top, sources, tests, requirement links and seed.\n"
        "Use the checked-in OpenRTL launcher and import these explicitly selected files\n"
        "into a new session. Review requirements and baseline before fresh execution.\n"
        "Provider/runtime setup and permissions remain separate. No install hook is included.\n"
        "Retained runs are historical evidence only. This is not a portable session backup,\n"
        "does not restore execution/recovery authority and does not execute exported Python.\n"
        "Passing simulation does not establish formal proof, synthesis/PPA or release approval.\n"
        "An INCOMPLETE file means export was interrupted; do not use that directory as complete.\n"
    ).encode()
    require(sum(len(data) for data in files.values()) <= 96 * 1024 * 1024, "export_exceeds_bound")
    rows = [{"path": p, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for p, data in sorted(files.items())]
    plan = {"schema": "openrtl.design-export.v1", "state_digest": content_digest(state), "revision": state["revision"],
            "status": state["status"], "files": rows, "provenance": state["imports"], "source_containing": True,
            "session_restore": False, "runtime_authority": False, "publication": False, "evidence_requested": include_evidence,
            "evidence": "explicitly-excluded" if not include_evidence else "reverified-retained-run" if state["simulation"] else "no-simulation-evidence"}
    return plan, files


def show_export(plan: JsonObject, destination: Path, emit: Callable[[str], None]) -> None:
    emit("Export source-containing design and evidence to new directory: " + str(destination))
    for row in plan["files"]:
        emit(row["path"] + " (" + str(row["size_bytes"]) + " bytes)")
    emit("State: " + plan["status"] + "; evidence: " + plan["evidence"])
    emit("Includes original imported references and current design sources. No session database, provider history or runtime authority.")


def export_design(store: DesignSessionStore, destination: Path, approved_digest: str, *, include_evidence: bool = True) -> JsonObject:
    plan, files = export_material(store, include_evidence=include_evidence)
    require(content_digest(plan) == approved_digest, "export_review_stale")
    target = safe_root(destination)
    require(not target.is_relative_to(store.root) and not store.root.is_relative_to(target), "export_must_be_outside_session")
    write_export(target, files, "openrtl-export.json", plan)
    return plan


def write_export(destination: Path, files: dict[str, bytes], manifest_name: str, plan: JsonObject) -> None:
    """Write reviewed material exclusively; leave incomplete output on interruption."""
    target = safe_root(destination)
    require(manifest_name not in files and "INCOMPLETE" not in files, "export_reserved_name")
    require(not target.exists() and target.parent.is_dir(), "export_destination_must_be_new")
    # Anchor every write to opened directory descriptors; never follow a swapped parent.
    parent_fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in target.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        os.mkdir(target.name, mode=0o700, dir_fd=parent_fd)  # Exclusive; cannot overwrite an existing empty directory.
        root_fd = os.open(target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    def write(relative: str, data: bytes) -> None:
        parts = PurePosixPath(relative).parts
        require(bool(parts) and not relative.startswith("/") and all(p not in (".", "..") for p in parts), "export_path_invalid")
        directory_fd = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                try: os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    try:
        write("INCOMPLETE", b"Interrupted exports are incomplete. Choose a new destination to retry.\n")
        for path, data in sorted(files.items()):
            write(path, data)
        write(manifest_name, canonical(plan) + b"\n")
        os.unlink("INCOMPLETE", dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
