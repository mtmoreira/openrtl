"""Validate the backend-neutral simulator image acquisition source and lock binding."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openrtl.domain.simulator_image_source import SOURCE_RELATIVE_PATH, validate_source  # noqa: E402


def _lock_artifacts(lock: object) -> dict[str, dict[str, object]]:
    if not isinstance(lock, dict) or not isinstance(lock.get("package"), list):
        raise ValueError("simulator_image_lock_invalid")
    result: dict[str, dict[str, object]] = {}
    for package in lock["package"]:
        if not isinstance(package, dict) or package.get("name") not in ("cocotb", "find-libpython"):
            continue
        name = package["name"]
        if name == "cocotb":
            artifact = package.get("sdist")
            distribution = "sdist"
        else:
            wheels = package.get("wheels")
            artifact = wheels[0] if isinstance(wheels, list) and len(wheels) == 1 else None
            distribution = "wheel"
        if not isinstance(artifact, dict) or set(artifact) != {"url", "hash", "size", "upload-time"}:
            raise ValueError("simulator_image_lock_artifact_invalid")
        digest = artifact["hash"]
        url = artifact["url"]
        if not isinstance(digest, str) or not digest.startswith("sha256:") or not isinstance(url, str):
            raise ValueError("simulator_image_lock_artifact_invalid")
        result[str(name)] = {"name": name, "version": package.get("version"), "distribution": distribution,
                             "filename": url.rsplit("/", 1)[-1], "url": url, "sha256": digest[7:],
                             "size": artifact["size"]}
    if set(result) != {"cocotb", "find-libpython"}:
        raise ValueError("simulator_image_lock_packages_missing")
    return result


def validate(root: Path) -> str:
    source_path = root / SOURCE_RELATIVE_PATH
    source_bytes = source_path.read_bytes()
    if not source_bytes.endswith(b"\n") or len(source_bytes) > 64 * 1024:
        raise ValueError("simulator_image_source_file_invalid")
    source = validate_source(json.loads(source_bytes))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked = _lock_artifacts(lock)
    for artifact in source["python_artifacts"]:
        if artifact != locked[artifact["name"]]:
            raise ValueError("simulator_image_lock_binding_changed")
    return "sha256:" + hashlib.sha256(source_bytes).hexdigest()


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parsed = parser.parse_args(arguments)
    digest = validate(parsed.root.resolve())
    print(f"CHECKPOINT simulator_image_source valid {digest}")
    print("OPENRTL_SIMULATOR_IMAGE_SOURCE_VALIDATION_STATUS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
