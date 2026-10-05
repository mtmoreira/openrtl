"""Bounded portable records, never an imported executable database or authority."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import cast

from openrtl.adapters.design_export import _read, write_export
from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.domain.design_session import (
    JsonObject, SESSION_SCHEMA, MAX_ARTIFACT_BYTES, canonical, content_digest,
    object_value, require, source_path, text, validate_state,
)

from openrtl.domain.design_events import event

MAX_BYTES = 96 * 1024 * 1024
MAX_ROWS = 4096

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document(data: bytes) -> JsonObject:
    value = json.loads(data)
    require(isinstance(value, dict) and canonical(value) + b"\n" == data, "portable_canonical_json_required")
    return cast(JsonObject, value)



def run_material(state: JsonObject, files: dict[str, bytes]) -> set[str]:
    """Verify copied historical run bindings without executing or contacting a runtime."""
    report = state["simulation"]
    if report is None: return set()
    require(report["schema"] == "openrtl.design-simulation.v2", "portable_runtime_bound_evidence_required")
    require(report["input_digest"] == content_digest({"spec": state["approved_spec"], "files": state["files"], "manifest": state["manifest"]}),
            "portable_run_input_changed")
    run_id = report["run_id"]
    require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id) is not None, "portable_run_id_invalid")
    prefix = "runs/" + run_id + "/"
    paths = {prefix + p for p in ("intent.json", "control/run.py", "evidence/report.json")}
    require(json.loads(files[prefix + "evidence/report.json"]) == report, "portable_report_changed")
    intent = object_value(json.loads(files[prefix + "intent.json"]),
                          {"schema", "operation_id", "container_name", "profile_digest", "input_digest"})
    require(intent == {"schema": "openrtl.design-runtime-intent.v1", "operation_id": run_id,
        "container_name": "openrtl-design-" + run_id, "profile_digest": report["runtime"]["profile_digest"],
        "input_digest": report["input_digest"]}, "portable_intent_changed")
    require("sha256:" + sha(files[prefix + "control/run.py"]) == report["runtime"]["runner_digest"], "portable_runner_changed")
    require(set(report["artifacts"]) == {"results.xml", "model-results.json", "waves.vcd", "runner.log", "toolchain.json"},
            "portable_evidence_incomplete")
    for name, entry in report["artifacts"].items():
        row = object_value(entry, {"path", "sha256", "bytes"})
        relative = prefix + "evidence/" + name
        require(row["path"] == relative, "portable_evidence_path_invalid")
        data = files[relative]
        require(type(row["bytes"]) is int and len(data) == row["bytes"] and sha(data) == row["sha256"], "portable_evidence_changed")
        paths.add(relative)
    if report["status"] == "passed":
        from openrtl.adapters.design_measurements import simulation_times
        simulation_times(files[prefix + "evidence/results.xml"], state["manifest"]["expected_tests"])
    return paths


def validate_records(records: JsonObject, files: dict[str, bytes]) -> list[JsonObject]:
    object_value(records, {"schema", "snapshots", "events"})
    require(records["schema"] == "openrtl.portable-records.v1", "portable_records_schema_invalid")
    snapshots = records["snapshots"]
    require(isinstance(snapshots, list) and 1 <= len(snapshots) <= 512, "portable_history_bound")
    require(isinstance(records["events"], list) and len(records["events"]) == len(snapshots) - 1, "portable_history_gap")
    expected = {"records.json"}
    states = []
    for revision, value in enumerate(snapshots):
        require(len(canonical(value)) <= 2 * 1024 * 1024, "portable_snapshot_bound")
        state = validate_state(value)
        require(state["revision"] == revision, "portable_revision_gap")
        states.append(state)
        references = [*state["files"].items(), *((p, r["digest"]) for p, r in state.get("imports", {}).items())]
        for path, digest in references:
            source_path(path)
            relative = "blobs/" + digest[7:] + ".txt"
            data = files[relative]
            require(len(data) <= MAX_ARTIFACT_BYTES and "sha256:" + sha(data) == digest, "portable_blob_changed")
            text(data.decode("utf-8"))
            expected.add(relative)
        expected.update(run_material(state, files))
    for i, row in enumerate(records["events"], 1): event(row, i)
    require(states[-1]["schema"] == SESSION_SCHEMA, "explicit_session_upgrade_required")
    require(states[-1]["active"] is None, "export_requires_reconciled_session")
    require(set(files) == expected, "portable_unreferenced_or_missing_file")
    return states


def portable_material(store: DesignSessionStore) -> tuple[JsonObject, dict[str, bytes]]:
    require(not store.connection.in_transaction, "portable_transaction_already_active")
    store.connection.execute("BEGIN")
    try:
        count, total, largest = store.connection.execute("SELECT COUNT(*), COALESCE(SUM(length(payload)),0), COALESCE(MAX(length(payload)),0) FROM snapshots").fetchone()
        require(1 <= count <= 512 and total <= 16 * 1024 * 1024 and largest <= 2 * 1024 * 1024, "portable_history_bound")
        count, total = store.connection.execute("SELECT COUNT(*), COALESCE(SUM(length(payload)),0) FROM events").fetchone()
        require(count <= 511 and total <= 8 * 1024 * 1024, "portable_history_bound")
        rows = store.connection.execute("SELECT revision, payload FROM snapshots ORDER BY revision LIMIT 513").fetchall()
        require(1 <= len(rows) <= 512, "portable_history_bound")
        snapshots = [validate_state(json.loads(row[1])) for row in rows]
        require([row[0] for row in rows] == list(range(len(rows))), "portable_revision_gap")
        require(snapshots[-1]["active"] is None, "export_requires_reconciled_session")
        events = [json.loads(row[0]) for row in store.connection.execute("SELECT payload FROM events ORDER BY sequence LIMIT 513")]
        files = {"records.json": canonical({"schema": "openrtl.portable-records.v1", "snapshots": snapshots, "events": events}) + b"\n"}
        for state in snapshots:
            for data in [*store.contents(state).values(), *store.import_contents(state).values()]:
                raw = data.encode(); files["blobs/" + sha(raw) + ".txt"] = raw
            report = state["simulation"]
            if report is not None:
                run_id = report["run_id"]
                require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id) is not None, "portable_run_id_invalid")
                prefix = "runs/" + run_id + "/"
                for name in ("intent.json", "control/run.py", "evidence/report.json", "evidence/results.xml", "evidence/model-results.json",
                             "evidence/waves.vcd", "evidence/runner.log", "evidence/toolchain.json"):
                    relative = prefix + name
                    if relative not in files: files[relative] = _read(store.root / relative, 16 * 1024 * 1024)
            require(sum(map(len, files.values())) <= MAX_BYTES, "portable_size_bound")
        validate_records(document(files["records.json"]), files)
    finally:
        store.connection.execute("ROLLBACK")
    require(store.read() == snapshots[-1], "portable_session_changed")
    plan = make_plan(files, snapshots[-1])
    return plan, files


def make_plan(files: dict[str, bytes], state: JsonObject) -> JsonObject:
    require(len(files) <= MAX_ROWS and sum(map(len, files.values())) <= MAX_BYTES and
            all(len(data) <= 16 * 1024 * 1024 for data in files.values()), "portable_size_bound")
    return {"schema": "openrtl.portable-session.v1", "source_containing": True, "runtime_authority": False,
            "state_digest": content_digest(state), "revision": state["revision"],
            "files": [{"path": path, "sha256": sha(data), "size_bytes": len(data)} for path, data in sorted(files.items())]}


def portable_input(root: Path) -> tuple[JsonObject, dict[str, bytes], list[JsonObject]]:
    root = safe_root(root)
    require(not (root / "INCOMPLETE").exists(), "session_restore_incomplete")
    plan = document(_read(root / "openrtl-session.json", 1024 * 1024))
    object_value(plan, {"schema", "source_containing", "runtime_authority", "state_digest", "revision", "files"})
    require(plan["schema"] == "openrtl.portable-session.v1" and plan["source_containing"] is True and plan["runtime_authority"] is False,
            "portable_schema_invalid")
    rows = plan["files"]
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS, "portable_file_bound")
    names = set(); total = 0
    for value in rows:
        row = object_value(value, {"path", "sha256", "size_bytes"}); path = row["path"]
        require(isinstance(path, str) and re.fullmatch(r"records\.json|blobs/[a-f0-9]{64}\.txt|runs/[a-f0-9]{32}/(?:intent\.json|control/run\.py|evidence/(?:report\.json|results\.xml|model-results\.json|waves\.vcd|runner\.log|toolchain\.json))", path) is not None,
                "portable_path_invalid")
        require(path not in names and isinstance(row["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is not None,
                "portable_file_duplicate_or_digest_invalid")
        require(type(row["size_bytes"]) is int and 0 <= row["size_bytes"] <= 16 * 1024 * 1024, "portable_size_bound")
        names.add(path); total += row["size_bytes"]
    require(total <= MAX_BYTES, "portable_size_bound")
    for row in rows:
        path = safe_root(root / row["path"]); info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == row["size_bytes"], "portable_file_changed")
    files = {}
    for row in rows:
        data = _read(root / row["path"], row["size_bytes"])
        require(len(data) == row["size_bytes"] and sha(data) == row["sha256"], "portable_file_changed")
        files[row["path"]] = data
    states = validate_records(document(files["records.json"]), files)
    require(make_plan(files, states[-1]) == plan, "portable_manifest_changed")
    return plan, files, states


def write_portable(store: DesignSessionStore, target: Path, approved: str) -> JsonObject:
    plan, files = portable_material(store)
    require(content_digest(plan) == approved, "export_review_stale")
    target = safe_root(target)
    require(not target.is_relative_to(store.root) and not store.root.is_relative_to(target), "export_must_be_outside_session")
    write_export(target, files, "openrtl-session.json", plan)
    return plan


def restore_portable(source: Path, target: Path, approved: str) -> JsonObject:
    plan, files, states = portable_input(source)
    require(content_digest(plan) == approved, "export_review_stale")
    target = safe_root(target); source = safe_root(source)
    require(not target.is_relative_to(source) and not source.is_relative_to(target), "export_must_be_outside_session")
    restored, output = restored_material(plan, files, states)
    write_export(target, output, "openrtl-restore.json", {"schema": "openrtl.session-restore.v1", "portable_digest": content_digest(plan),
                 "state_digest": content_digest(restored), "runtime_authority": False})
    return restored


def restored_material(plan: JsonObject, files: dict[str, bytes], states: list[JsonObject]) -> tuple[JsonObject, dict[str, bytes]]:
    """Build restore bytes independently of filesystem publication."""
    require(validate_records(document(files["records.json"]), files) == states and make_plan(files, states[-1]) == plan,
            "portable_manifest_changed")
    records = document(files["records.json"])
    restored = copy.deepcopy(states[-1]); restored["revision"] += 1
    validate_state(restored)
    receipt = {"schema": "openrtl.design-event.v1", "sequence": restored["revision"], "timestamp_ns": time.time_ns(),
               "event": "session.restored", "fields": {"output_digest": content_digest(plan)}}
    # Build a known schema from validated data. Never open/execute a supplied database.
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE snapshots (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE blobs (digest TEXT PRIMARY KEY, content BLOB NOT NULL)")
        connection.executemany("INSERT INTO snapshots VALUES (?, ?)", [(s["revision"], canonical(s).decode()) for s in [*states, restored]])
        connection.executemany("INSERT INTO events VALUES (?, ?)", [(e["sequence"], canonical(e).decode()) for e in [*records["events"], receipt]])
        connection.executemany("INSERT INTO blobs VALUES (?, ?)", [("sha256:" + p[6:-4], b) for p, b in files.items() if p.startswith("blobs/")])
        connection.commit(); database = connection.serialize()
    finally: connection.close()
    output = {p: data for p, data in files.items() if p.startswith("runs/")}
    output["session.sqlite3"] = database
    return restored, output
