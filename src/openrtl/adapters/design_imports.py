"""Explicit bounded source-only import; never execute collateral or import hooks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.domain.design_imports import validate_import_plan
from openrtl.domain.design_session import JsonObject, MAX_ARTIFACT_BYTES, MAX_CONTEXT_BYTES, SESSION_SCHEMA, content_digest, require, text


def import_design_files(store: DesignSessionStore, root: Path, value: object, approved_digest: str) -> JsonObject:
    plan = validate_import_plan(value)
    require(content_digest(plan) == approved_digest, "reviewed_import_digest_mismatch")
    state = store.read()
    require(state["schema"] == SESSION_SCHEMA and state["status"] == "discovery" and state["active"] is None,
            "imports_require_idle_discovery")
    selected_root = safe_root(root)
    require(selected_root.is_dir(), "import_root_unavailable")
    # Validate every path and metadata before reading any selected contents.
    selected: list[tuple[JsonObject, Path, os.stat_result]] = []
    total = 0
    for row in plan["files"]:
        path = safe_root(selected_root / row["source"])
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_ARTIFACT_BYTES,
                "import_file_type_or_size_invalid")
        total += info.st_size
        selected.append((row, path, info))
    require(total <= MAX_CONTEXT_BYTES, "import_batch_exceeds_bound")
    additions: list[JsonObject] = []
    for row, path, info in selected:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            observed = os.fstat(descriptor)
            require((observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns) ==
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), "import_file_changed")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_ARTIFACT_BYTES + 1)
            require(len(data) == info.st_size and "sha256:" + hashlib.sha256(data).hexdigest() == row["digest"],
                    "import_source_digest_mismatch")
        finally:
            os.close(descriptor)
        content = text(data.decode("utf-8"))
        expected = {"digest": row["digest"], "source": row["source"], "bytes": len(data), "plan_digest": approved_digest}
        existing = state["imports"].get(row["target"])
        require(existing is None or existing == expected, "import_target_already_owned")
        if existing is None:
            additions.append({"path": row["target"], "content": content, "source": row["source"],
                              "digest": row["digest"], "plan_digest": approved_digest})
    if not additions:
        store.import_contents(state)
        return state
    return store.save(state, state, "imports.recorded", {"output_digest": approved_digest, "artifact_count": len(additions)},
                      imports=additions)
