"""Local filesystem catalog for reusable verified design packages."""

from __future__ import annotations

import json
from pathlib import Path

from openrtl.domain.packages import DesignPackage, TrustLevel


class LocalDesignCatalog:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root == Path("/"):
            raise ValueError("catalog root must be absolute and bounded")
        self.root = root

    def package_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(sorted(path.name for path in self.root.iterdir() if path.is_dir()))

    def versions(self, package_id: str) -> tuple[str, ...]:
        package_root = self.root / package_id
        if not package_root.exists():
            return ()
        return tuple(
            sorted(
                (path.stem for path in package_root.glob("*.json")),
                key=_version_key,
            )
        )

    def store_manifest(self, package: DesignPackage) -> Path:
        if not package.publication_ready:
            raise ValueError("only verified package candidates may enter the catalog")
        directory = self.root / package.package_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{package.version}.json"
        if destination.exists():
            raise FileExistsError("package version already exists")
        payload = {
            "content_digest": package.content_digest,
            "design_id": package.design_id,
            "evidence_ids": package.evidence_ids,
            "files": [
                {"digest": item.content_digest, "kind": item.kind, "path": item.path}
                for item in package.files
            ],
            "license_id": package.license_id,
            "package_id": package.package_id,
            "trust": package.trust.value,
            "version": package.version,
        }
        destination.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        return destination

    def read_manifest(self, package_id: str, version: str) -> dict[str, object]:
        source = self.root / package_id / f"{version}.json"
        value = json.loads(source.read_text())
        if not isinstance(value, dict):
            raise ValueError("catalog manifest must be an object")
        if value.get("trust") == TrustLevel.UNVERIFIED.value:
            raise ValueError("catalog manifest is not verified")
        return value


def _version_key(value: str) -> tuple[int, int, int, str]:
    core, _, suffix = value.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, suffix
