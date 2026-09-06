"""Transactional project snapshots; no raw prompt or provider-session persistence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Iterator, cast
import uuid

from openrtl.domain.design_session import (
    JsonObject, MAX_ARTIFACT_BYTES, canonical, initial_state, require, source_path, validate_state, text,
)


def safe_root(path: Path) -> Path:
    selected = path.absolute()
    require(not any(part == ".." for part in selected.parts), "project_parent_traversal")
    require(not any(p.is_symlink() for p in (selected, *selected.parents)), "project_symlink")
    return selected


class DesignSessionStore:
    def __init__(self, root: Path, *, create: bool = False) -> None:
        self.root = safe_root(root)
        if create:
            require(not self.root.exists(), "new_project_must_be_absent")
            self.root.mkdir(mode=0o700)
        require(self.root.is_dir(), "project_unavailable")
        database = self.root / "session.sqlite3"
        for suffix in ("", "-wal", "-shm", "-journal"):
            selected = Path(str(database) + suffix)
            require(not selected.is_symlink() and (not selected.exists() or selected.is_file()),
                    "session_storage_unrecognized")
        require(create or database.is_file(), "session_database_missing")
        uri = database.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        self.connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        self.connection.execute("PRAGMA trusted_schema=OFF")
        self.connection.execute("PRAGMA synchronous=FULL")
        if create:
            with self.transaction():
                self.connection.execute("CREATE TABLE snapshots (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                self.connection.execute("CREATE TABLE blobs (digest TEXT PRIMARY KEY, content BLOB NOT NULL)")
                self.connection.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                self.connection.execute("INSERT INTO snapshots VALUES (0, ?)", (canonical(initial_state()).decode(),))
            os.chmod(database, 0o600)
        self.read()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def read(self) -> JsonObject:
        row = self.connection.execute("SELECT payload FROM snapshots ORDER BY revision DESC LIMIT 1").fetchone()
        require(row is not None, "session_snapshot_missing")
        require(len(row[0]) <= 2 * 1024 * 1024, "session_snapshot_too_large")
        return validate_state(json.loads(row[0]))

    def save(self, previous: JsonObject, updated: JsonObject, event: str,
             fields: JsonObject | None = None, *, files: list[JsonObject] | None = None) -> JsonObject:
        allowed = {"operation_id", "role", "context_digest", "output_digest", "error_code",
                   "input_tokens", "output_tokens", "elapsed_ms", "artifact_count", "spec_digest",
                   "provider", "model", "evidence_kind", "run_id"}
        safe_fields = dict(fields or {})
        require(set(safe_fields).issubset(allowed), "event_fields_not_allowlisted")
        require(all(type(v) in (str, int, bool) and len(str(v)) <= 256 for v in safe_fields.values()),
                "event_field_bounds_invalid")
        for value in safe_fields.values():
            if isinstance(value, str):
                text(value, maximum=256)
        require(event in {"spec.proposed", "spec.approved", "spec.revised", "operation.started",
                          "operation.completed", "operation.received", "operation.failed", "simulation.completed",
                          "simulation.failed", "review.completed", "project.accepted", "detail.changed"},
                "event_code_unrecognized")
        with self.transaction():
            require(self.read() == previous, "session_concurrent_change")
            current = json.loads(canonical(updated))
            for item in files or []:
                path = source_path(item["path"])
                data = text(item["content"]).encode("utf-8")
                require(len(data) <= MAX_ARTIFACT_BYTES, "artifact_too_large")
                digest = "sha256:" + hashlib.sha256(data).hexdigest()
                existing = self.connection.execute("SELECT content FROM blobs WHERE digest=?", (digest,)).fetchone()
                require(existing is None or existing[0] == data, "artifact_digest_collision")
                self.connection.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest, data))
                current["files"][path] = digest
            current["revision"] = previous["revision"] + 1
            validate_state(current)
            encoded_event = {"schema": "openrtl.design-event.v1", "sequence": current["revision"],
                             "timestamp_ns": time.time_ns(), "event": event, "fields": safe_fields}
            self.connection.execute("INSERT INTO snapshots VALUES (?, ?)",
                                    (current["revision"], canonical(current).decode()))
            self.connection.execute("INSERT INTO events VALUES (?, ?)",
                                    (current["revision"], canonical(encoded_event).decode()))
        return cast(JsonObject, current)

    def contents(self, state: JsonObject) -> dict[str, str]:
        result = {}
        for path, digest in state["files"].items():
            source_path(path)
            row = self.connection.execute("SELECT content FROM blobs WHERE digest=?", (digest,)).fetchone()
            require(row is not None and len(row[0]) <= MAX_ARTIFACT_BYTES and
                    "sha256:" + hashlib.sha256(row[0]).hexdigest() == digest,
                    "artifact_bytes_changed")
            result[path] = row[0].decode("utf-8")
        return result

    def materialize(self, state: JsonObject) -> Path:
        parent = safe_root(self.root / "runs")
        parent.mkdir(mode=0o700, exist_ok=True)
        output = parent / uuid.uuid4().hex
        output.mkdir(mode=0o700)
        inputs = output / "input"
        inputs.mkdir(mode=0o700)
        for relative, content in self.contents(state).items():
            selected = inputs / relative
            selected.parent.mkdir(parents=True, exist_ok=True)
            with selected.open("x", encoding="utf-8") as stream:
                stream.write(content)
        return output

    def events(self) -> tuple[JsonObject, ...]:
        return tuple(json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM events ORDER BY sequence"))
