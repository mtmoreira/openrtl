"""Transactional project snapshots; no raw prompt or provider-session persistence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Iterator, cast
import uuid

from openrtl.domain.design_session import (
    JsonObject, MAX_ARTIFACT_BYTES, SESSION_SCHEMA, WEB_SESSION_SCHEMA, COACHING_SESSION_SCHEMA, LEGACY_SESSION_SCHEMA, PREVIOUS_SESSION_SCHEMA, IMPORT_SESSION_SCHEMA, canonical, initial_state,
    require, source_path, validate_state, text, content_digest,
)


def safe_root(path: Path) -> Path:
    selected = path.absolute()
    require(not any(part == ".." for part in selected.parts), "project_parent_traversal")
    require(not any(p.is_symlink() for p in (selected, *selected.parents)), "project_symlink")
    return selected


class DesignSessionStore:
    def __init__(self, root: Path, *, create: bool = False, read_only: bool = False) -> None:
        require(not (create and read_only), "read_only_creation_invalid")
        self._lock_fd: int | None = None
        self._owned_operations: set[str] = set()
        self.root = safe_root(root)
        if create:
            require(not self.root.exists(), "new_project_must_be_absent")
            self.root.mkdir(mode=0o700)
        require(self.root.is_dir(), "project_unavailable")
        require(not (self.root / "INCOMPLETE").exists(), "session_restore_incomplete")
        database = self.root / "session.sqlite3"
        for suffix in ("", "-wal", "-shm", "-journal"):
            selected = Path(str(database) + suffix)
            require(not selected.is_symlink() and (not selected.exists() or selected.is_file()),
                    "session_storage_unrecognized")
            if selected.exists():
                info = selected.stat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "session_storage_unrecognized")
        require(create or database.is_file(), "session_database_missing")
        connection: sqlite3.Connection | None = None
        try:
            if not read_only:
                lock = safe_root(self.root / ".session.lock")
                require(not lock.exists() or lock.is_file(), "session_lock_unrecognized")
                self._lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                require(os.fstat(self._lock_fd).st_nlink == 1, "session_lock_link_invalid")
                os.set_inheritable(self._lock_fd, False)
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("session_writer_already_active") from None
            uri = database.as_uri() + ("?mode=ro" if read_only else "?mode=rwc" if create else "?mode=rw")
            connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
            self.connection = connection
            self.connection.execute("PRAGMA trusted_schema=OFF")
            if read_only:
                self.connection.execute("PRAGMA query_only=ON")
            else:
                self.connection.execute("PRAGMA synchronous=FULL")
            if create:
                with self.transaction():
                    self.connection.execute("CREATE TABLE snapshots (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                    self.connection.execute("CREATE TABLE blobs (digest TEXT PRIMARY KEY, content BLOB NOT NULL)")
                    self.connection.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                    self.connection.execute("INSERT INTO snapshots VALUES (0, ?)", (canonical(initial_state()).decode(),))
                os.chmod(database, 0o600)
            self.read()
        except BaseException:
            if connection is not None:
                connection.close()
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            raise

    @property
    def exclusive(self) -> bool:
        return self._lock_fd is not None

    def operation_owned(self, operation_id: str) -> bool:
        return operation_id in self._owned_operations

    def upgrade(self) -> JsonObject:
        """Explicit, append-only v1–v5 migration; prior snapshots remain unchanged."""
        state = self.read()
        if state["schema"] == SESSION_SCHEMA:
            return state
        require(state["schema"] in (LEGACY_SESSION_SCHEMA, PREVIOUS_SESSION_SCHEMA,
                                    IMPORT_SESSION_SCHEMA, COACHING_SESSION_SCHEMA,
                                    WEB_SESSION_SCHEMA), "session_upgrade_unrecognized")
        updated = {**state, **{k: v for k, v in initial_state().items() if k not in state},
                   "schema": SESSION_SCHEMA}
        updated["provider"]["prior_unpriced_calls"] = state["calls"]
        if state["schema"] == LEGACY_SESSION_SCHEMA:
            updated.update(approval_mode="legacy_user" if state["approved_spec"] else None,
                           acceptance_mode="legacy_user" if state["status"] == "accepted" else None)
        return self.save(state, updated, "session.upgraded")

    def close(self) -> None:
        self.connection.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        require(self.exclusive, "session_read_only")
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
             fields: JsonObject | None = None, *, files: list[JsonObject] | None = None,
             imports: list[JsonObject] | None = None) -> JsonObject:
        from openrtl.domain.design_events import EVENTS, FIELDS
        safe_fields = dict(fields or {})
        safe_fields.setdefault("spec_digest", content_digest(updated["spec"]))
        safe_fields.setdefault("requirements_digest", content_digest(updated["spec"]["requirements"] if updated["spec"] else []))
        safe_fields.setdefault("artifacts_digest", content_digest(updated["files"]))
        if previous["active"] is not None:
            safe_fields.setdefault("operation_id", previous["active"]["id"])
        require(set(safe_fields).issubset(FIELDS), "event_fields_not_allowlisted")
        require(all(type(v) in (str, int, bool) and len(str(v)) <= 256 for v in safe_fields.values()),
                "event_field_bounds_invalid")
        for value in safe_fields.values():
            if isinstance(value, str):
                text(value, maximum=256)
        require(event in EVENTS, "event_code_unrecognized")
        with self.transaction():
            require(self.read() == previous, "session_concurrent_change")
            current = json.loads(canonical(updated))
            for item in (files or []) + (imports or []):
                path = source_path(item["path"])
                data = text(item["content"]).encode("utf-8")
                require(len(data) <= MAX_ARTIFACT_BYTES, "artifact_too_large")
                digest = "sha256:" + hashlib.sha256(data).hexdigest()
                existing = self.connection.execute("SELECT content FROM blobs WHERE digest=?", (digest,)).fetchone()
                require(existing is None or existing[0] == data, "artifact_digest_collision")
                self.connection.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest, data))
                if "source" in item:
                    require(digest == item["digest"] and path not in current["imports"], "import_binding_or_collision_invalid")
                    current["imports"][path] = {"digest": digest, "source": item["source"],
                                                 "bytes": len(data), "plan_digest": item["plan_digest"]}
                else:
                    current["files"][path] = digest
            current["revision"] = previous["revision"] + 1
            validate_state(current)
            safe_fields["artifacts_digest"] = content_digest(current["files"])
            encoded_event = {"schema": "openrtl.design-event.v1", "sequence": current["revision"],
                             "timestamp_ns": time.time_ns(), "event": event, "fields": safe_fields}
            self.connection.execute("INSERT INTO snapshots VALUES (?, ?)",
                                    (current["revision"], canonical(current).decode()))
            self.connection.execute("INSERT INTO events VALUES (?, ?)",
                                    (current["revision"], canonical(encoded_event).decode()))
        if event == "operation.started":
            self._owned_operations.add(current["active"]["id"])
        elif previous["active"] is not None and current["active"] is None:
            self._owned_operations.discard(previous["active"]["id"])
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

    def import_contents(self, state: JsonObject) -> dict[str, str]:
        return self.contents({"files": {p: row["digest"] for p, row in state.get("imports", {}).items()}})

    def historical_state(self, revision: int) -> JsonObject:
        require(type(revision) is int and revision >= 0, "historical_revision_invalid")
        row = self.connection.execute("SELECT payload FROM snapshots WHERE revision=?", (revision,)).fetchone()
        require(row is not None and len(row[0]) <= 2 * 1024 * 1024, "historical_snapshot_unavailable")
        state = validate_state(json.loads(row[0]))
        self.contents(state)
        return state

    def measurement(self, state: JsonObject) -> JsonObject:
        from openrtl.adapters.design_measurements import measured_run
        self.contents(state)
        return measured_run(self.root, state)

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

    def events_after(self, cursor: int, *, limit: int = 64) -> tuple[JsonObject, ...]:
        require(type(cursor) is int and cursor >= 0 and type(limit) is int and 1 <= limit <= 128,
                "event_cursor_invalid")
        rows = self.connection.execute(
            "SELECT payload FROM events WHERE sequence > ? ORDER BY sequence LIMIT ?", (cursor, limit)
        )
        return tuple(json.loads(row[0]) for row in rows)
