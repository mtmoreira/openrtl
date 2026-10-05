"""Revision-bound, read-only engineering views shared by local clients."""

from __future__ import annotations

import difflib
from typing import Any

from openrtl.application.design_agent import design_input_digest
from openrtl.application.design_waveforms import DesignWaveforms
from openrtl.domain.design_session import JsonObject, content_digest, require, source_path


class DesignWorkbench:
    def __init__(self, store: Any) -> None:
        self.store = store
        self.waveforms = DesignWaveforms(store)
        self._elaborated: JsonObject | None = None

    def use_elaborated_index(self, index: JsonObject) -> None:
        """Accept only an index bound to the exact saved source revision."""
        require(isinstance(index, dict) and index.get("schema") == "openrtl.verilator-hierarchy.v1" and
                index.get("status") == "elaborated" and index.get("tool") == "verilator" and
                index.get("tool_version") == "5.046" and isinstance(index.get("instances"), list),
                "workbench_hierarchy_invalid")
        revision = index.get("revision")
        state = self._state(revision)
        require(state["spec"] is not None and
                index.get("input_digest") == design_input_digest(state) and
                index.get("top") == state["spec"]["top"], "workbench_hierarchy_input_stale")
        for row in index["instances"]:
            require(isinstance(row, dict), "workbench_hierarchy_invalid")
            for field in ("definition", "instantiation"):
                source = row.get(field)
                require(isinstance(source, dict) and source.get("revision") == revision and
                        state["files"].get(source.get("path")) == source.get("digest"),
                        "workbench_hierarchy_source_stale")
                content = self.store.contents(state)[source["path"]]
                require(type(source.get("line")) is int and
                        1 <= source["line"] <= len(content.splitlines()),
                        "workbench_hierarchy_line_invalid")
        self._elaborated = index

    def index_compiler_output(self, tree: bytes, metadata: bytes, *, revision: int,
                              source_root: str, tool_version: str,
                              options: list[str]) -> JsonObject:
        """Import trusted adapter output without exposing an arbitrary browser upload."""
        from openrtl.adapters.verilator_hierarchy import parse_verilator_hierarchy
        state = self._state(revision)
        require(state["spec"] is not None and state["manifest"] is not None,
                "workbench_hierarchy_inputs_missing")
        index = parse_verilator_hierarchy(
            tree, metadata, revision=revision, source_root=source_root,
            sources=state["files"], top=state["manifest"]["top"],
            input_digest=design_input_digest(state), tool_version=tool_version,
            options=options)
        self.use_elaborated_index(index)
        return index

    def _state(self, revision: int) -> JsonObject:
        require(type(revision) is int and revision >= 0, "workbench_revision_invalid")
        current = self.store.read()
        require(revision <= current["revision"], "workbench_revision_future")
        return current if revision == current["revision"] else self.store.historical_state(revision)

    def inventory(self, revision: int) -> JsonObject:
        state = self._state(revision)
        files = [{"path": path, "digest": digest, "revision": revision,
                  "kind": path.split("/", 1)[0]}
                 for path, digest in sorted(state["files"].items())]
        spec = state["spec"]
        manifest = state["manifest"]
        requirement_tests = manifest["requirement_tests"] if manifest else []
        linked = {row["requirement_id"]: row["tests"] for row in requirement_tests}
        requirements = [{"id": row["id"], "text": row["text"],
                         "planned_tests": linked.get(row["id"], []),
                         "link_status": "planned_not_coverage"}
                        for row in spec["requirements"]] if spec else []
        return {"schema": "openrtl.workbench-inventory.v1", "revision": revision,
                "current_revision": self.store.read()["revision"],
                "input_digest": design_input_digest(state), "files": files,
                "requirements": requirements,
                "decisions": [row for row in state["engineering_memory"] if row["kind"] == "decision"],
                "planned": {"top": spec["top"] if spec else None,
                            "test_modules": manifest["test_modules"] if manifest else [],
                            "expected_tests": manifest["expected_tests"] if manifest else [],
                            "requirement_tests": requirement_tests},
                "elaborated": (self._elaborated if self._elaborated is not None and
                               self._elaborated["revision"] == revision and
                               self._elaborated["input_digest"] == design_input_digest(state)
                               else {"status": "unavailable", "reason": "compiler_index_not_available"}),
                "proposal": state["proposal"],
                "history": [{"revision": event["sequence"], "event": event["event"]}
                            for event in self.store.events_after(max(0, revision - 64), limit=64)
                            if event["sequence"] <= revision]}

    def source(self, revision: int, path: str, digest: str) -> JsonObject:
        source_path(path)
        state = self._state(revision)
        require(state["files"].get(path) == digest, "workbench_source_identity_stale")
        content = self.store.contents(state)[path]
        return {"schema": "openrtl.workbench-source.v1", "path": path,
                "revision": revision, "digest": digest, "content": content,
                "current": revision == self.store.read()["revision"]}

    def diff(self, before: int, after: int, path: str) -> JsonObject:
        source_path(path)
        require(type(before) is int and type(after) is int and 0 <= before <= after,
                "workbench_diff_revisions_invalid")
        old, new = self._state(before), self._state(after)
        require(path in old["files"] or path in new["files"], "workbench_diff_path_unknown")
        previous = self.store.contents(old).get(path, "")
        current = self.store.contents(new).get(path, "")
        lines = list(difflib.unified_diff(previous.splitlines(keepends=True),
                                          current.splitlines(keepends=True),
                                          fromfile=f"r{before}/{path}", tofile=f"r{after}/{path}"))
        require(sum(len(line.encode("utf-8")) for line in lines) <= 512 * 1024,
                "workbench_diff_too_large")
        return {"schema": "openrtl.workbench-diff.v1", "path": path,
                "before_revision": before, "after_revision": after,
                "before_digest": old["files"].get(path), "after_digest": new["files"].get(path),
                "content": "".join(lines)}

    def attachment(self, value: object) -> JsonObject:
        if isinstance(value, dict) and value.get("kind") == "waveform":
            return self.waveforms.attachment(value)
        require(isinstance(value, dict) and set(value) == {"revision", "path", "digest", "start_line", "end_line"},
                "workbench_attachment_invalid")
        row: JsonObject = value
        source = self.source(row["revision"], row["path"], row["digest"])
        lines = source["content"].splitlines()
        start, end = row["start_line"], row["end_line"]
        require(type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                and end - start < 200, "workbench_attachment_range_invalid")
        return {"revision": source["revision"], "path": source["path"],
                "digest": source["digest"], "start_line": start, "end_line": end,
                "selection_digest": content_digest(lines[start - 1:end])}
