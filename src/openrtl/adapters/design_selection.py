"""Explicit file selection and complete previews; never sweep or execute sources."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable

from openrtl.adapters.design_session_store import safe_root
from openrtl.domain.design_imports import import_source, validate_import_plan
from openrtl.domain.design_session import JsonObject, MAX_ARTIFACT_BYTES, MAX_CONTEXT_BYTES, require, source_path, text


def select_paths(tokens: list[str]) -> list[tuple[str, str]]:
    require(0 < len(tokens) <= 64, "select_between_one_and_64_files")
    result = []
    for token in tokens:
        source, separator, target = token.partition("=")
        import_source(source)
        path = PurePosixPath(source)
        if not separator:
            if path.parts[0] in ("rtl", "model", "dv", "docs"):
                target = source
            elif path.suffix in (".sv", ".svh", ".v", ".vh"):
                target = "rtl/" + path.name
            elif path.suffix in (".md", ".txt", ".json"):
                target = "docs/" + path.name
            else:
                raise ValueError("python_role_required_use_source_equals_model_or_dv_target")
        source_path(target)
        import_source(target)
        require(PurePosixPath(target).suffix == path.suffix, "selection_extension_changed")
        result.append((source, target))
    require(len({target for _, target in result}) == len(result), "selection_target_collision")
    return result


def selection(root: Path, tokens: list[str]) -> tuple[JsonObject, dict[str, str], tuple[int, int]]:
    pairs = select_paths(tokens)  # All names, including late sensitive paths, before any read.
    selected_root = safe_root(root)
    root_info = selected_root.stat()
    require(stat.S_ISDIR(root_info.st_mode), "import_root_unavailable")
    selected = []
    total = 0
    for source, target in pairs:
        path = safe_root(selected_root / source)
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_ARTIFACT_BYTES,
                "import_file_type_or_size_invalid")
        total += info.st_size
        selected.append((source, target, path, info))
    require(total <= MAX_CONTEXT_BYTES, "import_batch_exceeds_bound")
    rows, contents = [], {}
    for source, target, path, info in selected:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            observed = os.fstat(stream.fileno())
            require(stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1 and
                    (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns) ==
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), "import_file_changed")
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
        require(len(data) == info.st_size, "import_file_changed")
        contents[target] = text(data.decode("utf-8"))
        rows.append({"source": source, "target": target, "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    return validate_import_plan({"schema": "openrtl.design-import.v1", "files": rows}), contents, (root_info.st_dev, root_info.st_ino)


def show_selection(plan: JsonObject, contents: dict[str, str], emit: Callable[[str], None]) -> None:
    emit("Import preview: selected UTF-8 LF files only; no source will be executed or overwritten.")
    for row in plan["files"]:
        content = contents[row["target"]]
        emit(row["source"] + " -> " + row["target"] + " (" + str(len(content.encode())) + " bytes)")
        emit("Untrusted source content begins:")
        emit(content)
        emit("Untrusted source content ends.")
    emit("Imported files remain reference material until separately reviewed adoption or completion.")
