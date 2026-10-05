"""Opt-in private inspection records, separate from engineering state and exports.

These records are never engineering input or executable artifacts. The ordinary
portable-session exporter deliberately excludes both private tables.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from agentrig.core.private_trace import redact_private_trace_content

from openrtl.domain.design_session import JsonObject, canonical, require

MAX_RECORD_BYTES = 512 * 1024
MAX_CONTENT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 8192
_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "credential",
                "credentials", "access_token", "refresh_token", "client_secret",
                "cookie", "set_cookie", "private_key"}
_SECRET_TEXT = re.compile(
    r"(?i)(?:\bBearer\s+[^\s\"']+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|password|access_token|refresh_token|client_secret)"
    r"[\"']?\s*[:=]\s*[\"']?[^\s,\"'}]+)"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)


def _bounded_payload(payload: JsonObject) -> tuple[JsonObject, bool]:
    truncated = False
    visited = 0

    def clean(value: Any, depth: int = 0) -> Any:
        nonlocal truncated, visited
        visited += 1
        if visited > 4096 or depth > 16:
            truncated = True
            return "[omitted: trace structure limit]"
        if isinstance(value, Mapping):
            output = {}
            for key, child in value.items():
                require(isinstance(key, str) and len(key) <= 256, "private_trace_key_invalid")
                output[key] = ("[redacted]" if key.lower().replace("-", "_") in _SECRET_KEYS
                               else clean(child, depth + 1))
                if visited > 4096:
                    break
            return output
        if isinstance(value, (list, tuple)):
            output = []
            for child in value:
                output.append(clean(child, depth + 1))
                if visited > 4096:
                    break
            return output
        if isinstance(value, str):
            result = _SECRET_TEXT.sub("[redacted]", _PRIVATE_KEY.sub("[redacted private key]", value))
            encoded = result.encode("utf-8")
            if len(encoded) > MAX_CONTENT_BYTES:
                truncated = True
                result = encoded[:MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
            return result
        require(value is None or type(value) in (int, float, bool), "private_trace_value_invalid")
        return value

    result = clean(redact_private_trace_content(payload))
    encoded = canonical(result)
    if len(encoded) > MAX_RECORD_BYTES:
        result = {"omitted": "record_size_limit", "preview": encoded[:64 * 1024].decode("utf-8", errors="ignore")}
        truncated = True
    return result, truncated


class DesignTraceStore:
    """Use the session's existing exclusive connection; never open another file."""

    def __init__(self, session_store: Any, *, enabled: bool = False) -> None:
        self._session = session_store
        self.enabled = False
        if enabled:
            self.set_enabled(True)

    def __repr__(self) -> str:
        return f"DesignTraceStore(enabled={self.enabled!r})"

    def _available(self) -> bool:
        row = self._session.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_traces'"
        ).fetchone()
        return row is not None

    def set_enabled(self, enabled: bool) -> None:
        require(type(enabled) is bool, "private_trace_setting_invalid")
        require(self._session.exclusive, "session_read_only")
        if enabled:
            with self._session.transaction():
                self._session.connection.execute(
                    "CREATE TABLE IF NOT EXISTS private_traces ("
                    "id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, category TEXT NOT NULL, "
                    "timestamp_ns INTEGER NOT NULL, payload TEXT NOT NULL, content_bytes INTEGER NOT NULL, "
                    "truncated INTEGER NOT NULL)"
                )
                self._session.connection.execute(
                    "CREATE INDEX IF NOT EXISTS private_trace_operation ON private_traces(operation_id, id)"
                )
                self._session.connection.execute(
                    "CREATE TABLE IF NOT EXISTS private_trace_limits (id INTEGER PRIMARY KEY, omitted INTEGER NOT NULL)"
                )
                self._session.connection.execute("INSERT OR IGNORE INTO private_trace_limits VALUES (1, 0)")
        self.enabled = enabled

    def record(self, operation_id: str, category: str, payload: JsonObject) -> None:
        if not self.enabled:
            return
        require(isinstance(operation_id, str) and re.fullmatch(r"[a-f0-9]{32}", operation_id) is not None,
                "private_trace_operation_invalid")
        require(isinstance(category, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", category) is not None,
                "private_trace_category_invalid")
        require(isinstance(payload, dict), "private_trace_payload_invalid")
        bounded, truncated = _bounded_payload(payload)
        encoded = canonical(bounded)
        with self._session.transaction():
            count, size = self._session.connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(content_bytes), 0) FROM private_traces"
            ).fetchone()
            if count >= MAX_RECORDS or size + len(encoded) > MAX_TOTAL_BYTES:
                self._session.connection.execute("UPDATE private_trace_limits SET omitted=omitted+1 WHERE id=1")
                return
            self._session.connection.execute(
                "INSERT INTO private_traces(operation_id, category, timestamp_ns, payload, content_bytes, truncated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (operation_id, category, time.time_ns(), encoded.decode(), len(encoded), int(truncated)),
            )

    def records(self, operation_ids: set[str]) -> list[JsonObject]:
        require(isinstance(operation_ids, set) and len(operation_ids) <= 256 and all(
            isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier)
            for identifier in operation_ids), "private_trace_operation_invalid")
        if not operation_ids or not self._available():
            return []
        placeholders = ",".join("?" for _ in operation_ids)
        count, size, largest = self._session.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(length(CAST(payload AS BLOB))), 0), "
            "COALESCE(MAX(length(CAST(payload AS BLOB))), 0) "
            f"FROM private_traces WHERE operation_id IN ({placeholders})", tuple(sorted(operation_ids)),
        ).fetchone()
        require(count <= MAX_RECORDS and size <= MAX_TOTAL_BYTES and largest <= MAX_RECORD_BYTES,
                "private_trace_read_limit")
        rows = self._session.connection.execute(
            "SELECT id, operation_id, category, timestamp_ns, payload, content_bytes, truncated "
            f"FROM private_traces WHERE operation_id IN ({placeholders}) ORDER BY id LIMIT ?",
            (*sorted(operation_ids), MAX_RECORDS),
        )
        result = []
        for identifier, operation, category, timestamp, payload, size, truncated in rows:
            require(type(identifier) is int and 0 < identifier < 2**63 and
                    isinstance(operation, str) and operation in operation_ids and
                    isinstance(category, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", category) is not None and
                    type(timestamp) is int and 0 <= timestamp < 2**63 and
                    type(truncated) is int and truncated in (0, 1) and
                    type(size) is int and 0 <= size <= MAX_RECORD_BYTES and
                    isinstance(payload, str) and len(payload.encode()) == size,
                    "private_trace_record_invalid")
            content = json.loads(payload)
            require(isinstance(content, dict) and canonical(content).decode() == payload,
                    "private_trace_record_invalid")
            clean_content, bounded = _bounded_payload(content)
            require(clean_content == content and (not bounded or bool(truncated)), "private_trace_record_invalid")
            result.append({"schema": "openrtl.private-trace.v1", "id": identifier,
                           "operation_id": operation, "category": category,
                           "timestamp_ns": timestamp, "payload": content,
                           "content_bytes": size, "truncated": bool(truncated)})
        return result

    def status(self) -> JsonObject:
        available = self._available()
        omitted = 0
        if available:
            row = self._session.connection.execute("SELECT omitted FROM private_trace_limits WHERE id=1").fetchone()
            require(row is not None and type(row[0]) is int and row[0] >= 0, "private_trace_limits_invalid")
            omitted = row[0]
        return {"enabled": self.enabled, "available": available, "exported": False,
                "omitted_records": omitted, "limits": {"record_bytes": MAX_RECORD_BYTES,
                "content_bytes": MAX_CONTENT_BYTES, "total_bytes": MAX_TOTAL_BYTES, "records": MAX_RECORDS}}
