"""Explicit bounded session discovery and source-free support bundles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3

from openrtl.adapters.design_export import write_export
from openrtl.domain.design_events import event
from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.application.design_diagnostics import diagnostic, safe_event
from openrtl.domain.design_session import JsonObject, canonical, content_digest, require


def discover(root: Path, *, offset: int = 0, limit: int = 20) -> JsonObject:
    root = safe_root(root)
    require(type(offset) is int and 0 <= offset <= 1000 and type(limit) is int and 1 <= limit <= 100, "session_list_bounds")
    require(root.is_dir(), "project_unavailable")
    names: list[str] = []
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(names) < 1000, "session_directory_bound")
            # Include all entries in the bound, but inspect only ordinary named directories.
            names.append(entry.name)
    result = []
    selected = sorted(names)[offset:offset + limit]
    for name in selected:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}", name) is None: continue
        path = root / name
        if path.is_symlink() or not path.is_dir(): continue
        if not (path / "session.sqlite3").exists() and not (path / "INCOMPLETE").exists(): continue
        try:
            store = DesignSessionStore(path, read_only=True)
            try:
                state = store.read()
                result.append({"name": name, "schema": state["schema"], "revision": state["revision"], "status": state["status"],
                               "calls": state["calls"], "warnings": len(state.get("warnings", [])), "recovery_required": state["active"] is not None})
            finally: store.close()
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            result.append({"name": name, "status": "unavailable", "action": "Inspect this explicitly selected project with sessions inspect."})
    return {"schema": "openrtl.session-list.v1", "sessions": result,
            "next_offset": offset + limit if offset + limit < len(names) else None, "scanned_entries": len(selected)}


def inspect_session(store: DesignSessionStore, *, limit: int = 50) -> JsonObject:
    require(type(limit) is int and 1 <= limit <= 200, "session_event_limit_invalid")
    require(not store.connection.in_transaction, "portable_transaction_already_active")
    store.connection.execute("BEGIN")
    try:
        state = store.read()
        count, total, largest = store.connection.execute("SELECT COUNT(*), COALESCE(SUM(length(payload)),0), COALESCE(MAX(length(payload)),0) FROM events").fetchone()
        require(count <= 10000 and total <= 16 * 1024 * 1024 and largest <= 16384, "session_diagnostics_event_bound")
        raw = store.connection.execute("SELECT sequence, payload FROM events ORDER BY sequence LIMIT 10001").fetchall()
        require(len(raw) <= 10000, "session_diagnostics_event_bound")
        events = []
        for sequence, payload in raw:
            require(len(payload.encode()) <= 16384, "session_event_size_bound")
            events.append(event(json.loads(payload), sequence))
        known: dict[str, dict[str, int]] = {"input_tokens": {}, "output_tokens": {}}
        for row in events:
            fields = row["fields"]; operation = fields.get("operation_id")
            if row["event"] == "operation.received" and isinstance(operation, str) and re.fullmatch(r"[a-f0-9]{32}", operation):
                for key in known:
                    value = fields.get(key)
                    if type(value) is int and 0 <= value < 2**63: known[key][operation] = value
        usage = {key: {"known_total": sum(values.values()) if values else None, "known_calls": len(values),
                       "unknown_calls": max(0, state["calls"] - len(values))} for key, values in known.items()}
        active = state["active"]
        limits = state.get("limits")
        delegation = state.get("delegation")
        return {"schema": "openrtl.session-diagnostics.v1", "session_schema": state["schema"], "revision": state["revision"],
                "state_digest": content_digest(state), "status": state["status"], "stage": state["stage"],
                "calls": state["calls"], "repairs": state["repairs"], "limits": state.get("limits"),
                "remaining_calls": max(0, limits["max_calls"] - state["calls"]) if limits else None,
                "remaining_repairs": max(0, limits["max_repairs"] - state["repairs"]) if limits else None,
                "delegation": {"steps": delegation["steps"], "max_steps": delegation["authorization"]["max_steps"],
                               "deadline_ns": delegation["deadline_ns"], "digest": delegation["digest"]} if delegation else None,
                "warnings": len(state.get("warnings", [])), "unreviewed_warnings": sum(w["review"] == "pending" for w in state.get("warnings", [])),
                "files": len(state["files"]), "imports": len(state.get("imports", {})), "usage": usage,
                "active": {"id": active["id"], "kind": active["kind"], "stage": active["stage"]} if active else None,
                "last_error": diagnostic(ValueError(state["last_error"])) if state["last_error"] else None,
                "events": [safe_event(e) for e in events[-limit:]], "omitted_events": max(0, len(events) - limit),
                "source_containing": False, "runtime_authority": False, "upload": False}
    finally: store.connection.execute("ROLLBACK")


def support_bundle(store: DesignSessionStore, target: Path, approved: str | None = None) -> JsonObject:
    plan = inspect_session(store)
    if approved is not None:
        require(content_digest(plan) == approved, "export_review_stale")
        target = safe_root(target)
        require(not target.is_relative_to(store.root) and not store.root.is_relative_to(target), "export_must_be_outside_session")
        write_export(target, {"diagnostics.json": canonical(plan) + b"\n"}, "openrtl-support.json",
                     {"schema": "openrtl.support-bundle.v1", "digest": content_digest(plan), "source_containing": False, "upload": False})
    return plan
