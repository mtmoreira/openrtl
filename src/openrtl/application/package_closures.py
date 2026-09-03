"""Deterministic dependency-closure contracts for portable design packages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from openrtl.domain import DesignPackage, PackageDependency
from openrtl.domain._validation import digest, identifier, nonempty


@dataclass(frozen=True, order=True)
class PackageBundlePin:
    package_id: str
    version: str
    manifest_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", identifier(self.package_id, "package_id"))
        object.__setattr__(self, "version", nonempty(self.version, "version"))
        object.__setattr__(self, "manifest_digest", digest(self.manifest_digest, "manifest_digest"))


@dataclass(frozen=True, order=True)
class LockedPackage:
    package_id: str
    version: str
    package_digest: str
    manifest_digest: str
    dependencies: tuple[PackageDependency, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", identifier(self.package_id, "package_id"))
        object.__setattr__(self, "version", nonempty(self.version, "version"))
        object.__setattr__(self, "package_digest", digest(self.package_digest, "package_digest"))
        object.__setattr__(self, "manifest_digest", digest(self.manifest_digest, "manifest_digest"))
        dependencies = tuple(sorted(self.dependencies))
        if len({value.package_id for value in dependencies}) != len(dependencies):
            raise ValueError("locked package dependencies must be unique")
        object.__setattr__(self, "dependencies", dependencies)


@dataclass(frozen=True)
class PackageClosureLock:
    root_package_id: str
    root_version: str
    packages: tuple[LockedPackage, ...]
    install_order: tuple[str, ...]

    def __post_init__(self) -> None:
        root_id = identifier(self.root_package_id, "root package_id")
        root_version = nonempty(self.root_version, "root version")
        packages = tuple(sorted(self.packages, key=lambda value: value.package_id))
        package_ids = tuple(value.package_id for value in packages)
        order = tuple(identifier(value, "install_order package_id") for value in self.install_order)
        if not packages or len(set(package_ids)) != len(package_ids):
            raise ValueError("package closure lock packages must be nonempty and unique")
        if len(set(order)) != len(order) or set(order) != set(package_ids):
            raise ValueError("package closure install order must contain every package exactly once")
        if order[-1] != root_id or not any(
            value.package_id == root_id and value.version == root_version for value in packages
        ):
            raise ValueError("package closure root identity is invalid")
        object.__setattr__(self, "root_package_id", root_id)
        object.__setattr__(self, "root_version", root_version)
        object.__setattr__(self, "packages", packages)
        object.__setattr__(self, "install_order", order)

    def payload(self) -> dict[str, object]:
        return {
            "install_order": list(self.install_order),
            "packages": [
                {
                    "dependencies": [
                        {
                            "content_digest": dependency.content_digest,
                            "package_id": dependency.package_id,
                            "version": dependency.version,
                        }
                        for dependency in package.dependencies
                    ],
                    "manifest_digest": package.manifest_digest,
                    "package_digest": package.package_digest,
                    "package_id": package.package_id,
                    "version": package.version,
                }
                for package in self.packages
            ],
            "root": {
                "package_id": self.root_package_id,
                "version": self.root_version,
            },
            "schema": "openrtl.package-closure-lock.v1",
        }

    @property
    def content_digest(self) -> str:
        encoded = json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class PackageClosureMaterializationReport:
    root_package_id: str
    root_version: str
    lock_digest: str
    destination: str
    install_order: tuple[str, ...]
    materialization_receipts: tuple[str, ...]
    receipt_uri: str


def dependency_install_order(
    root_package_id: str,
    root_version: str,
    packages: tuple[DesignPackage, ...],
) -> tuple[DesignPackage, ...]:
    """Return an exact dependency-first closure or reject an ambiguous graph."""
    normalized_root = identifier(root_package_id, "root package_id")
    by_id: dict[str, DesignPackage] = {}
    for package in packages:
        existing = by_id.get(package.package_id)
        if existing is not None:
            raise ValueError(f"package closure has conflicting pins for {package.package_id}")
        by_id[package.package_id] = package
    root = by_id.get(normalized_root)
    if root is None or root.version != root_version:
        raise ValueError("package closure root identity is missing")

    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[DesignPackage] = []

    def visit(package: DesignPackage) -> None:
        if package.package_id in visiting:
            raise ValueError(f"package dependency cycle includes {package.package_id}")
        if package.package_id in visited:
            return
        visiting.add(package.package_id)
        for dependency in package.dependencies:
            selected = by_id.get(dependency.package_id)
            if selected is None:
                raise ValueError(f"package dependency is missing: {dependency.package_id}")
            if selected.package_id in visiting:
                raise ValueError(f"package dependency cycle includes {selected.package_id}")
            visit(selected)
            if selected.version != dependency.version:
                raise ValueError(f"package dependency version mismatch: {dependency.package_id}")
            if selected.content_digest != dependency.content_digest:
                raise ValueError(f"package dependency digest mismatch: {dependency.package_id}")
        visiting.remove(package.package_id)
        visited.add(package.package_id)
        ordered.append(package)

    visit(root)
    unused = set(by_id) - visited
    if unused:
        raise ValueError("package closure contains unused pins: " + ", ".join(sorted(unused)))
    return tuple(ordered)
