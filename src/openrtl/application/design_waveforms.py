"""Run-bound, bounded waveform queries shared by local clients."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from typing import Any

from openrtl.adapters.design_session_store import safe_root
from openrtl.adapters.waveforms import VcdIndex
from openrtl.application.design_agent import design_input_digest
from openrtl.domain.design_session import JsonObject, canonical, require


MAX_BROWSER_TRACE_BYTES = 16 * 1024 * 1024
MAX_BROWSER_TIME_FS = 2**53 - 1
MAX_SIGNAL_NAMES = 4096
MAX_SELECTED_SIGNALS = 8
MAX_WINDOW_TRANSITIONS = 256


class DesignWaveforms:
    """Read only retained VCD evidence; never adopt a trace by pathname alone."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._cache_key: tuple[str, str] | None = None
        self._cache_index: VcdIndex | None = None

    def _reports(self) -> list[JsonObject]:
        cursor = 0
        found: list[JsonObject] = []
        scanned = 0
        while scanned < 4096:
            events = self.store.events_after(cursor, limit=128)
            if not events:
                break
            for event in events:
                if event["event"] == "simulation.completed":
                    state = self.store.historical_state(event["sequence"])
                    report = state["simulation"]
                    require(isinstance(report, dict) and
                            report.get("run_id") == event["fields"].get("run_id") and
                            report.get("input_digest") == design_input_digest(state),
                            "waveform_run_history_invalid")
                    found.append({"run_id": report["run_id"], "revision": event["sequence"],
                                  "input_digest": report["input_digest"],
                                  "status": report["status"], "report": report})
                    found = found[-32:]
            cursor = events[-1]["sequence"]
            scanned += len(events)
            if len(events) < 128:
                break
        require(scanned < 4096 or not self.store.events_after(cursor, limit=1),
                "waveform_run_history_limit")
        return found

    def runs(self) -> JsonObject:
        current = self.store.read()
        rows = []
        for record in reversed(self._reports()):
            report = record["report"]
            artifact = report.get("artifacts", {}).get("waves.vcd")
            trace_status = "missing"
            if isinstance(artifact, dict):
                candidate = self.store.root / "runs" / record["run_id"] / "evidence" / "waves.vcd"
                try:
                    candidate = safe_root(candidate)
                    if candidate.is_file():
                        trace_status = "available" if candidate.stat().st_size <= MAX_BROWSER_TRACE_BYTES else "over_limit"
                except (ValueError, OSError):
                    trace_status = "missing"
            rows.append({"run_id": record["run_id"], "revision": record["revision"],
                         "input_digest": record["input_digest"], "status": record["status"],
                         "current_input": record["input_digest"] == design_input_digest(current),
                         "trace_status": trace_status})
        return {"schema": "openrtl.waveform-runs.v1", "runs": rows}

    def _record(self, run_id: object) -> JsonObject:
        require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id) is not None,
                "waveform_run_id_invalid")
        matches = [record for record in self._reports() if record["run_id"] == run_id]
        require(len(matches) == 1, "waveform_run_unknown")
        return matches[0]

    def _index(self, record: JsonObject) -> tuple[VcdIndex, str]:
        run_id = record["run_id"]
        artifact = record["report"].get("artifacts", {}).get("waves.vcd")
        require(isinstance(artifact, dict) and set(artifact) == {"sha256", "bytes", "path"},
                "waveform_trace_missing")
        relative = "runs/" + run_id + "/evidence/waves.vcd"
        require(artifact["path"] == relative and isinstance(artifact["sha256"], str) and
                re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]) is not None and
                type(artifact["bytes"]) is int and 0 < artifact["bytes"] <= MAX_BROWSER_TRACE_BYTES,
                "waveform_trace_metadata_invalid")
        path = safe_root(self.store.root / relative)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise ValueError("waveform_trace_missing") from None
        try:
            metadata = os.fstat(descriptor)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                    metadata.st_size == artifact["bytes"], "waveform_trace_changed")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(artifact["bytes"] + 1)
        except OSError:
            raise ValueError("waveform_trace_missing") from None
        finally:
            os.close(descriptor)
        require(len(data) == artifact["bytes"], "waveform_trace_changed")
        digest = hashlib.sha256(data).hexdigest()
        require(digest == artifact["sha256"], "waveform_trace_changed")
        key = (run_id, digest)
        if self._cache_key != key or self._cache_index is None:
            try:
                index = VcdIndex.parse(data.decode("utf-8"), max_transitions=500_000)
            except UnicodeDecodeError:
                raise ValueError("waveform_trace_unsupported") from None
            except ValueError as error:
                raise ValueError("waveform_browser_limit" if str(error) ==
                                 "VCD transition count exceeds its bound" else
                                 "waveform_trace_unsupported") from None
            require(len(index.signal_names) <= MAX_SIGNAL_NAMES and
                    index.end_time_fs <= MAX_BROWSER_TIME_FS,
                    "waveform_browser_limit")
            require(all(len(name) <= 240 and all(32 <= ord(char) < 127 for char in name)
                        for name in index.signal_names) and
                    all(len(row.value) <= 256 for signal in index.signal_names
                        for row in index.transitions(signal)), "waveform_browser_limit")
            self._cache_key, self._cache_index = key, index
        return self._cache_index, "sha256:" + digest

    def catalog(self, run_id: object, search: object = "") -> JsonObject:
        record = self._record(run_id)
        require(isinstance(search, str) and len(search) <= 128 and
                all(32 <= ord(char) < 127 for char in search), "waveform_search_invalid")
        index, trace_digest = self._index(record)
        names = [name for name in index.signal_names if search.lower() in name.lower()]
        return {"schema": "openrtl.waveform-catalog.v1", "run_id": record["run_id"],
                "revision": record["revision"], "input_digest": record["input_digest"],
                "trace_digest": trace_digest, "status": record["status"],
                "timescale_fs": index.timescale_fs, "end_fs": index.end_time_fs,
                "total_matches": len(names), "signal_names": names[:128],
                "truncated": len(names) > 128}

    def query(self, run_id: object, trace_digest: object, signals: object,
              start_fs: object, end_fs: object, limit: object = 128) -> JsonObject:
        record = self._record(run_id)
        index, actual_digest = self._index(record)
        require(trace_digest == actual_digest, "waveform_trace_identity_stale")
        require(isinstance(signals, list) and 1 <= len(signals) <= MAX_SELECTED_SIGNALS and
                all(isinstance(name, str) and name in index.signal_names for name in signals) and
                len(set(signals)) == len(signals), "waveform_signals_invalid")
        require(type(start_fs) is int and type(end_fs) is int and
                0 <= start_fs <= end_fs <= index.end_time_fs and
                type(limit) is int and 1 <= limit <= MAX_WINDOW_TRANSITIONS,
                "waveform_window_invalid")
        selected = []
        for name in signals:
            # One extra transition distinguishes an exact window from truncation.
            transitions = index.transitions(name, start_fs, end_fs, limit=limit + 1)
            selected.append({"name": name, "value_at_start": index.value_at(name, start_fs),
                             "transitions": [{"timestamp_fs": row.timestamp_fs, "value": row.value}
                                             for row in transitions[:limit]],
                             "truncated": len(transitions) > limit})
        result = {"schema": "openrtl.waveform-window.v1", "run_id": record["run_id"],
                "revision": record["revision"], "input_digest": record["input_digest"],
                "trace_digest": actual_digest, "status": record["status"],
                "timescale_fs": index.timescale_fs, "trace_end_fs": index.end_time_fs,
                "start_fs": start_fs, "end_fs": end_fs, "selected_signals": selected}
        require(len(canonical(result)) <= 64 * 1024, "waveform_response_limit")
        return result

    def attachment(self, value: object) -> JsonObject:
        require(isinstance(value, dict) and set(value) ==
                {"kind", "run_id", "trace_digest", "signals", "start_fs", "end_fs"} and
                value["kind"] == "waveform", "waveform_attachment_invalid")
        result = self.query(value["run_id"], value["trace_digest"], value["signals"],
                            value["start_fs"], value["end_fs"], limit=16)
        require(len(canonical(result)) <= 16 * 1024, "waveform_attachment_too_large")
        return {"kind": "waveform", "run_id": result["run_id"],
                "revision": result["revision"], "input_digest": result["input_digest"],
                "trace_digest": result["trace_digest"], "signals": value["signals"],
                "start_fs": result["start_fs"], "end_fs": result["end_fs"]}
