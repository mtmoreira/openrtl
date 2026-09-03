"""Self-contained, digest-bound local package bundles and materialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, cast

from openrtl.application.package_candidates import (
    PackageMaterializationReport,
    PortablePackage,
    PortablePackageReceipt,
    VerifiedPackageCandidate,
)
from openrtl.domain import (
    DesignPackage,
    InterfacePort,
    InterfaceRequirement,
    PackageDependency,
    PackageFile,
    Parameter,
    PortDirection,
    TrustLevel,
    analyze_compatibility,
)
from openrtl.domain._validation import digest, identifier, relative_path


_SCHEMA = "openrtl.portable-design-package.v1"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_FILE_BYTES = 64 * 1024 * 1024
_SUPPORT_KINDS = {
    "simulation-profile",
    "simulation-evidence",
    "simulation-log",
    "simulation-results",
    "simulation-waveform",
}


class PortableDesignCatalog:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root == Path("/"):
            raise ValueError("portable catalog root must be absolute and bounded")
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise ValueError("portable catalog root must be a non-symlink directory")
            self.root = root.resolve(strict=True)
        else:
            self.root = root.parent.resolve(strict=True) / root.name

    def package_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(sorted(path.name for path in self.root.iterdir() if path.is_dir() and not path.is_symlink()))

    def versions(self, package_id: str) -> tuple[str, ...]:
        package_root = self.root / identifier(package_id, "package_id")
        if not package_root.exists():
            return ()
        return tuple(
            sorted(
                path.name
                for path in package_root.iterdir()
                if path.is_dir() and not path.is_symlink() and _VERSION.fullmatch(path.name)
            )
        )

    def store_candidate(
        self,
        source_root: Path,
        candidate: VerifiedPackageCandidate,
    ) -> PortablePackageReceipt:
        return self.store_package(source_root, candidate.package, candidate.supporting_files)

    def store_package(
        self,
        source_root: Path,
        package: DesignPackage,
        supporting_files: tuple[PackageFile, ...],
    ) -> PortablePackageReceipt:
        resolved_source = source_root.resolve(strict=True)
        if not package.publication_ready or not supporting_files:
            raise ValueError("portable catalog requires a fully verified package candidate")
        supporting_kinds = {value.kind for value in supporting_files}
        if supporting_kinds != _SUPPORT_KINDS or len(supporting_files) != len(_SUPPORT_KINDS):
            raise ValueError("portable package supporting evidence is incomplete")
        package_root = self.root / package.package_id
        if package_root.exists() and (package_root.is_symlink() or not package_root.is_dir()):
            raise ValueError("portable package root must be a non-symlink directory")
        destination = package_root / package.version
        temporary = package_root / f".{package.version}.openrtl-tmp"
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("portable package version already exists")
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError("portable package temporary target already exists")
        package_root.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        try:
            package_records = tuple(
                self._copy_record(resolved_source, temporary, value, "package")
                for value in package.files
            )
            support_records = tuple(
                self._copy_record(resolved_source, temporary, value, "evidence")
                for value in supporting_files
            )
            payload = {
                "content_digest": package.content_digest,
                "dependencies": [
                    {
                        "content_digest": value.content_digest,
                        "package_id": value.package_id,
                        "version": value.version,
                    }
                    for value in package.dependencies
                ],
                "design_id": package.design_id,
                "evidence_ids": list(package.evidence_ids),
                "files": package_records,
                "license_id": package.license_id,
                "package_id": package.package_id,
                "parameters": [
                    {
                        "default": value.default,
                        "maximum": value.maximum,
                        "minimum": value.minimum,
                        "name": value.name,
                    }
                    for value in package.parameters
                ],
                "ports": [
                    {
                        "clock_domain": value.clock_domain,
                        "direction": value.direction.value,
                        "name": value.name,
                        "signed": value.signed,
                        "width": value.width,
                    }
                    for value in package.ports
                ],
                "schema": _SCHEMA,
                "supporting_files": support_records,
                "trust": package.trust.value,
                "version": package.version,
            }
            manifest = temporary / "manifest.json"
            manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest_digest = f"sha256:{_sha256(manifest.read_bytes())}"
            temporary.rename(destination)
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        return PortablePackageReceipt(
            package.package_id,
            package.version,
            (destination / "manifest.json").relative_to(self.root).as_posix(),
            manifest_digest,
            package.content_digest,
        )

    def read_package(
        self,
        package_id: str,
        version: str,
        expected_manifest_digest: str,
    ) -> PortablePackage:
        normalized_id = identifier(package_id, "package_id")
        if not _VERSION.fullmatch(version):
            raise ValueError("version must use semantic version syntax")
        expected_digest = digest(expected_manifest_digest, "expected manifest digest")
        bundle = self.root / normalized_id / version
        manifest = _bounded_file(self.root, bundle / "manifest.json", _MAX_MANIFEST_BYTES, "bundle manifest")
        observed_digest = f"sha256:{_sha256(manifest)}"
        if observed_digest != expected_digest:
            raise ValueError("portable package manifest digest mismatch")
        payload = _json_object(manifest, "bundle manifest")
        _exact_keys(
            payload,
            {
                "content_digest", "dependencies", "design_id", "evidence_ids", "files",
                "license_id", "package_id", "parameters", "ports", "schema",
                "supporting_files", "trust", "version",
            },
            "bundle manifest",
        )
        if payload["schema"] != _SCHEMA or payload["package_id"] != normalized_id or payload["version"] != version:
            raise ValueError("portable package manifest identity is invalid")
        files = tuple(self._read_record(bundle, value, "package") for value in _list(payload["files"], "files"))
        supporting = tuple(
            self._read_record(bundle, value, "evidence")
            for value in _list(payload["supporting_files"], "supporting_files")
        )
        if {value.kind for value in supporting} != _SUPPORT_KINDS or len(supporting) != len(_SUPPORT_KINDS):
            raise ValueError("portable package supporting evidence is incomplete")
        package = DesignPackage(
            normalized_id,
            version,
            _string(payload["design_id"], "design_id"),
            _string(payload["license_id"], "license_id"),
            TrustLevel(_string(payload["trust"], "trust")),
            tuple(_port(value) for value in _list(payload["ports"], "ports")),
            tuple(_parameter(value) for value in _list(payload["parameters"], "parameters")),
            files,
            tuple(_string(value, "evidence_id") for value in _list(payload["evidence_ids"], "evidence_ids")),
            tuple(_dependency(value) for value in _list(payload["dependencies"], "dependencies")),
        )
        if package.content_digest != payload["content_digest"]:
            raise ValueError("portable package content digest mismatch")
        return PortablePackage(
            package,
            (bundle / "manifest.json").relative_to(self.root).as_posix(),
            observed_digest,
            supporting,
        )

    def materialize(
        self,
        package_id: str,
        version: str,
        expected_manifest_digest: str,
        destination: Path,
        required_ports: tuple[InterfaceRequirement, ...],
        parameter_values: tuple[tuple[str, int | bool | str], ...] = (),
    ) -> PackageMaterializationReport:
        portable = self.read_package(package_id, version, expected_manifest_digest)
        compatibility = analyze_compatibility(portable.package, required_ports, parameter_values)
        if not compatibility.compatible:
            raise ValueError("portable package is incompatible: " + "; ".join(compatibility.reasons))
        if not destination.is_absolute() or destination == Path("/"):
            raise ValueError("materialization destination must be absolute and bounded")
        selected = destination
        if selected.exists() or selected.is_symlink():
            raise FileExistsError("materialization destination already exists")
        selected.parent.mkdir(parents=True, exist_ok=True)
        temporary = selected.parent / f".{selected.name}.openrtl-tmp"
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError("materialization temporary target already exists")
        temporary.mkdir()
        materialized: list[str] = []
        bundle = self.root / portable.package.package_id / portable.package.version
        try:
            for value in portable.package.files:
                source = bundle / "payload/package" / value.path
                content = _bounded_file(bundle, source, _MAX_FILE_BYTES, "package payload")
                if f"sha256:{_sha256(content)}" != value.content_digest:
                    raise ValueError("portable package changed during materialization")
                output = temporary / value.path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
                materialized.append(value.path)
            receipt_payload = {
                "bundle_manifest_digest": portable.manifest_digest,
                "compatibility": {
                    "compatible": True,
                    "interface_digest": compatibility.interface_digest,
                },
                "files": [
                    {"content_digest": value.content_digest, "path": value.path}
                    for value in portable.package.files
                ],
                "package_digest": portable.package.content_digest,
                "package_id": portable.package.package_id,
                "parameter_values": [list(value) for value in parameter_values],
                "schema": "openrtl.package-materialization.v1",
                "version": portable.package.version,
            }
            receipt = temporary / "openrtl-package-materialization.json"
            receipt.write_text(json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.rename(selected)
        except Exception:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        return PackageMaterializationReport(
            portable.package.package_id,
            portable.package.version,
            portable.package.content_digest,
            portable.manifest_digest,
            selected.as_posix(),
            tuple(sorted(materialized)),
            (selected / "openrtl-package-materialization.json").as_posix(),
        )

    def _copy_record(
        self,
        source_root: Path,
        temporary: Path,
        value: PackageFile,
        category: str,
    ) -> dict[str, object]:
        content = _bounded_file(source_root, source_root / value.path, _MAX_FILE_BYTES, value.kind)
        if f"sha256:{_sha256(content)}" != value.content_digest:
            raise ValueError("portable package source digest mismatch")
        if category == "package":
            bundle_path = f"payload/package/{value.path}"
        else:
            suffix = Path(value.path).suffix
            bundle_path = f"payload/evidence/{value.kind}{suffix}"
        output = temporary / bundle_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        return {
            "bundle_path": bundle_path,
            "content_digest": value.content_digest,
            "kind": value.kind,
            "size_bytes": len(content),
            "source_path": value.path,
        }

    def _read_record(self, bundle: Path, value: object, category: str) -> PackageFile:
        record = _object(value, "file record")
        _exact_keys(record, {"bundle_path", "content_digest", "kind", "size_bytes", "source_path"}, "file record")
        source_path = relative_path(_string(record["source_path"], "source path"), "source path")
        kind = identifier(_string(record["kind"], "file kind"), "file kind")
        expected_prefix = "payload/package/" if category == "package" else "payload/evidence/"
        bundle_path = relative_path(_string(record["bundle_path"], "bundle path"), "bundle path")
        if not bundle_path.startswith(expected_prefix):
            raise ValueError("portable package bundle path category is invalid")
        if category == "package" and bundle_path != f"payload/package/{source_path}":
            raise ValueError("portable package payload path does not match its source path")
        if category == "evidence":
            expected = f"payload/evidence/{kind}{Path(source_path).suffix}"
            if bundle_path != expected:
                raise ValueError("portable package evidence path does not match its kind")
        content_digest = digest(_string(record["content_digest"], "content digest"), "content digest")
        size = record["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError("portable package file size is invalid")
        content = _bounded_file(bundle, bundle / bundle_path, _MAX_FILE_BYTES, "bundle payload")
        if len(content) != size or f"sha256:{_sha256(content)}" != content_digest:
            raise ValueError("portable package payload digest or size mismatch")
        return PackageFile(source_path, kind, content_digest)


def _bounded_file(root: Path, candidate: Path, maximum: int, label: str) -> bytes:
    try:
        lexical = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} is outside its bounded root") from error
    if ".." in lexical.parts:
        raise ValueError(f"{label} path is invalid")
    current = root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} path contains a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"{label} is missing") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} is outside its bounded root")
    content = resolved.read_bytes()
    if not content or len(content) > maximum:
        raise ValueError(f"{label} size is invalid")
    return content


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error


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


def _port(value: object) -> InterfacePort:
    item = _object(value, "port")
    _exact_keys(item, {"clock_domain", "direction", "name", "signed", "width"}, "port")
    width = item["width"]
    signed = item["signed"]
    clock_domain = item["clock_domain"]
    if not isinstance(width, int) or isinstance(width, bool) or not isinstance(signed, bool):
        raise ValueError("portable package port fields are invalid")
    if clock_domain is not None and not isinstance(clock_domain, str):
        raise ValueError("portable package clock domain is invalid")
    return InterfacePort(
        _string(item["name"], "port name"),
        PortDirection(_string(item["direction"], "port direction")),
        width,
        clock_domain,
        signed,
    )


def _parameter(value: object) -> Parameter:
    item = _object(value, "parameter")
    _exact_keys(item, {"default", "maximum", "minimum", "name"}, "parameter")
    default = item["default"]
    minimum = item["minimum"]
    maximum = item["maximum"]
    if not isinstance(default, (int, bool, str)):
        raise ValueError("portable package parameter default is invalid")
    if minimum is not None and (not isinstance(minimum, int) or isinstance(minimum, bool)):
        raise ValueError("portable package parameter minimum is invalid")
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool)):
        raise ValueError("portable package parameter maximum is invalid")
    return Parameter(_string(item["name"], "parameter name"), default, minimum, maximum)


def _dependency(value: object) -> PackageDependency:
    item = _object(value, "dependency")
    _exact_keys(item, {"content_digest", "package_id", "version"}, "dependency")
    return PackageDependency(
        _string(item["package_id"], "dependency package_id"),
        _string(item["version"], "dependency version"),
        _string(item["content_digest"], "dependency content_digest"),
    )
