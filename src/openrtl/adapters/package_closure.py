"""Digest-pinned dependency closure locking and atomic materialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, cast

from openrtl.adapters.portable_catalog import PortableDesignCatalog
from openrtl.application.package_closures import (
    LockedPackage,
    PackageBundlePin,
    PackageClosureLock,
    PackageClosureMaterializationReport,
    dependency_install_order,
)
from openrtl.domain import InterfaceRequirement, PackageDependency
from openrtl.domain._validation import digest


_MAX_LOCK_BYTES = 4 * 1024 * 1024


class DependencyClosedCatalog:
    def __init__(self, root: Path) -> None:
        self.portable = PortableDesignCatalog(root)

    def resolve(
        self,
        root_package_id: str,
        root_version: str,
        pins: tuple[PackageBundlePin, ...],
    ) -> PackageClosureLock:
        if not pins or len({value.package_id for value in pins}) != len(pins):
            raise ValueError("package closure pins must be nonempty and unique by package_id")
        by_id = {value.package_id: value for value in pins}
        packages = tuple(
            self.portable.read_package(value.package_id, value.version, value.manifest_digest).package
            for value in sorted(pins)
        )
        ordered = dependency_install_order(root_package_id, root_version, packages)
        locked = tuple(
            LockedPackage(
                package.package_id,
                package.version,
                package.content_digest,
                by_id[package.package_id].manifest_digest,
                package.dependencies,
            )
            for package in packages
        )
        return PackageClosureLock(
            root_package_id,
            root_version,
            locked,
            tuple(value.package_id for value in ordered),
        )

    def write_lock(self, lock: PackageClosureLock, output: Path) -> str:
        if not output.is_absolute() or output == Path("/"):
            raise ValueError("package closure lock output must be absolute and bounded")
        if output.exists() or output.is_symlink():
            raise FileExistsError("package closure lock output already exists")
        try:
            parent = output.parent.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("package closure lock parent is missing") from error
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("package closure lock parent must be a non-symlink directory")
        selected = parent / output.name
        temporary = parent / f".{output.name}.openrtl-tmp"
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError("package closure lock temporary target already exists")
        content = json.dumps(lock.payload(), indent=2, sort_keys=True).encode() + b"\n"
        try:
            temporary.write_bytes(content)
            temporary.rename(selected)
        except Exception:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
            raise
        return f"sha256:{_sha256(content)}"

    def read_lock(self, lock_path: Path, expected_lock_digest: str) -> PackageClosureLock:
        expected = digest(expected_lock_digest, "expected lock digest")
        if not lock_path.is_absolute() or lock_path == Path("/") or lock_path.is_symlink():
            raise ValueError("package closure lock path must be an absolute non-symlink file")
        try:
            selected = lock_path.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("package closure lock is missing") from error
        if not selected.is_file() or selected.stat().st_size < 1 or selected.stat().st_size > _MAX_LOCK_BYTES:
            raise ValueError("package closure lock size is invalid")
        content = selected.read_bytes()
        if f"sha256:{_sha256(content)}" != expected:
            raise ValueError("package closure lock digest mismatch")
        payload = _object(json.loads(content), "package closure lock")
        _exact_keys(payload, {"install_order", "packages", "root", "schema"}, "package closure lock")
        if payload["schema"] != "openrtl.package-closure-lock.v1":
            raise ValueError("package closure lock schema is invalid")
        root = _object(payload["root"], "package closure root")
        _exact_keys(root, {"package_id", "version"}, "package closure root")
        packages = tuple(_locked_package(value) for value in _list(payload["packages"], "packages"))
        lock = PackageClosureLock(
            _string(root["package_id"], "root package_id"),
            _string(root["version"], "root version"),
            packages,
            tuple(_string(value, "install order") for value in _list(payload["install_order"], "install_order")),
        )
        if lock.payload() != payload:
            raise ValueError("package closure lock is not in canonical form")
        return lock

    def materialize(
        self,
        lock_path: Path,
        expected_lock_digest: str,
        destination: Path,
        required_ports: tuple[InterfaceRequirement, ...],
        parameter_values: tuple[tuple[str, int | bool | str], ...] = (),
    ) -> PackageClosureMaterializationReport:
        lock = self.read_lock(lock_path, expected_lock_digest)
        portable_by_id = {
            value.package_id: self.portable.read_package(
                value.package_id,
                value.version,
                value.manifest_digest,
            )
            for value in lock.packages
        }
        packages = tuple(value.package for value in portable_by_id.values())
        ordered = dependency_install_order(lock.root_package_id, lock.root_version, packages)
        if tuple(value.package_id for value in ordered) != lock.install_order:
            raise ValueError("package closure install order does not match its dependency graph")
        for locked in lock.packages:
            package = portable_by_id[locked.package_id].package
            if (
                package.version != locked.version
                or package.content_digest != locked.package_digest
                or package.dependencies != locked.dependencies
            ):
                raise ValueError(f"locked package identity changed: {locked.package_id}")
        if not destination.is_absolute() or destination == Path("/"):
            raise ValueError("package closure destination must be absolute and bounded")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("package closure destination already exists")
        try:
            parent = destination.parent.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("package closure destination parent is missing") from error
        temporary = parent / f".{destination.name}.openrtl-tmp"
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError("package closure temporary target already exists")
        temporary.mkdir()
        receipts: list[str] = []
        try:
            for package in ordered:
                locked = next(value for value in lock.packages if value.package_id == package.package_id)
                report = self.portable.materialize(
                    package.package_id,
                    package.version,
                    locked.manifest_digest,
                    temporary / "packages" / package.package_id,
                    required_ports if package.package_id == lock.root_package_id else (),
                    parameter_values if package.package_id == lock.root_package_id else (),
                )
                receipts.append(
                    Path(report.receipt_uri).relative_to(temporary).as_posix()
                )
            receipt_payload = {
                "install_order": list(lock.install_order),
                "lock_digest": expected_lock_digest,
                "package_receipts": receipts,
                "root": {
                    "package_id": lock.root_package_id,
                    "version": lock.root_version,
                },
                "schema": "openrtl.package-closure-materialization.v1",
            }
            receipt = temporary / "openrtl-package-closure.json"
            receipt.write_text(json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.rename(destination)
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        return PackageClosureMaterializationReport(
            lock.root_package_id,
            lock.root_version,
            expected_lock_digest,
            destination.as_posix(),
            lock.install_order,
            tuple(receipts),
            (destination / "openrtl-package-closure.json").as_posix(),
        )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return cast(list[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields are invalid")


def _dependency(value: object) -> PackageDependency:
    item = _object(value, "locked dependency")
    _exact_keys(item, {"content_digest", "package_id", "version"}, "locked dependency")
    return PackageDependency(
        _string(item["package_id"], "dependency package_id"),
        _string(item["version"], "dependency version"),
        _string(item["content_digest"], "dependency content_digest"),
    )


def _locked_package(value: object) -> LockedPackage:
    item = _object(value, "locked package")
    _exact_keys(
        item,
        {"dependencies", "manifest_digest", "package_digest", "package_id", "version"},
        "locked package",
    )
    return LockedPackage(
        _string(item["package_id"], "locked package_id"),
        _string(item["version"], "locked version"),
        _string(item["package_digest"], "locked package digest"),
        _string(item["manifest_digest"], "locked manifest digest"),
        tuple(_dependency(value) for value in _list(item["dependencies"], "dependencies")),
    )
