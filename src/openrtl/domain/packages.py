"""Reusable design packages and deterministic compatibility analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re

from openrtl.domain._validation import digest, identifier, nonempty, relative_path
from openrtl.domain.design import InterfacePort, Parameter, PortDirection


_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_LICENSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")


class TrustLevel(str, Enum):
    UNVERIFIED = "unverified"
    SIMULATION_VERIFIED = "simulation_verified"
    FORMAL_VERIFIED = "formal_verified"
    IMPLEMENTATION_PROVEN = "implementation_proven"


@dataclass(frozen=True, order=True)
class PackageFile:
    path: str
    kind: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", relative_path(self.path))
        object.__setattr__(self, "kind", identifier(self.kind, "file kind"))
        object.__setattr__(self, "content_digest", digest(self.content_digest, "content_digest"))


@dataclass(frozen=True, order=True)
class PackageDependency:
    package_id: str
    version: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", identifier(self.package_id, "package_id"))
        _validate_version(self.version)
        object.__setattr__(self, "content_digest", digest(self.content_digest, "content_digest"))


@dataclass(frozen=True)
class DesignPackage:
    package_id: str
    version: str
    design_id: str
    license_id: str
    trust: TrustLevel
    ports: tuple[InterfacePort, ...]
    parameters: tuple[Parameter, ...]
    files: tuple[PackageFile, ...]
    evidence_ids: tuple[str, ...]
    dependencies: tuple[PackageDependency, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", identifier(self.package_id, "package_id"))
        _validate_version(self.version)
        object.__setattr__(self, "design_id", identifier(self.design_id, "design_id"))
        license_id = self.license_id.strip()
        if not _LICENSE.fullmatch(license_id) or license_id in {"NOASSERTION", "NONE"}:
            raise ValueError("license_id must be an explicit SPDX-style identifier")
        object.__setattr__(self, "license_id", license_id)
        ports = tuple(sorted(self.ports))
        parameters = tuple(sorted(self.parameters))
        files = tuple(sorted(self.files))
        evidence = tuple(identifier(value, "evidence_id") for value in self.evidence_ids)
        dependencies = tuple(sorted(self.dependencies))
        _unique(tuple(value.name for value in ports), "port names")
        _unique(tuple(value.name for value in parameters), "parameter names")
        _unique(tuple(value.path for value in files), "package paths")
        _unique(evidence, "evidence IDs")
        _unique(tuple(value.package_id for value in dependencies), "dependency package IDs")
        if not files:
            raise ValueError("a design package must contain files")
        if self.trust is not TrustLevel.UNVERIFIED and not evidence:
            raise ValueError("verified packages require validation evidence")
        object.__setattr__(self, "ports", ports)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "dependencies", dependencies)

    @property
    def content_digest(self) -> str:
        payload = {
            "dependencies": [
                (value.package_id, value.version, value.content_digest)
                for value in self.dependencies
            ],
            "design_id": self.design_id,
            "evidence_ids": self.evidence_ids,
            "files": [(value.path, value.kind, value.content_digest) for value in self.files],
            "license_id": self.license_id,
            "package_id": self.package_id,
            "parameters": [
                (value.name, value.default, value.minimum, value.maximum)
                for value in self.parameters
            ],
            "ports": [
                (
                    value.name,
                    value.direction.value,
                    value.width,
                    value.clock_domain,
                    value.signed,
                )
                for value in self.ports
            ],
            "trust": self.trust.value,
            "version": self.version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @property
    def publication_ready(self) -> bool:
        return self.trust is not TrustLevel.UNVERIFIED and bool(self.evidence_ids)


@dataclass(frozen=True)
class InterfaceRequirement:
    name: str
    direction: PortDirection
    width: int
    signed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", identifier(self.name, "port name"))
        if self.width < 1:
            raise ValueError("port width must be positive")


@dataclass(frozen=True)
class CompatibilityReport:
    package_id: str
    compatible: bool
    reasons: tuple[str, ...]
    interface_digest: str


def analyze_compatibility(
    package: DesignPackage,
    required_ports: tuple[InterfaceRequirement, ...],
    parameter_values: tuple[tuple[str, int | bool | str], ...] = (),
) -> CompatibilityReport:
    reasons: list[str] = []
    package_ports = {port.name: port for port in package.ports}
    for requirement in required_ports:
        available = package_ports.get(requirement.name)
        if available is None:
            reasons.append(f"missing port {requirement.name}")
            continue
        if available.direction is not requirement.direction:
            reasons.append(f"direction mismatch for {requirement.name}")
        if available.width != requirement.width:
            reasons.append(f"width mismatch for {requirement.name}")
        if available.signed != requirement.signed:
            reasons.append(f"signedness mismatch for {requirement.name}")
    package_parameters = {parameter.name: parameter for parameter in package.parameters}
    seen_parameters: set[str] = set()
    for name, value in parameter_values:
        normalized = identifier(name, "parameter name")
        if normalized in seen_parameters:
            raise ValueError("parameter values must be unique")
        seen_parameters.add(normalized)
        parameter = package_parameters.get(normalized)
        if parameter is None:
            reasons.append(f"unknown parameter {normalized}")
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            if parameter.minimum is not None and value < parameter.minimum:
                reasons.append(f"parameter {normalized} is below minimum")
            if parameter.maximum is not None and value > parameter.maximum:
                reasons.append(f"parameter {normalized} is above maximum")
        elif type(value) is not type(parameter.default):
            reasons.append(f"parameter type mismatch for {normalized}")
    encoded = json.dumps(
        [
            (value.name, value.direction.value, value.width, value.signed)
            for value in sorted(required_ports, key=lambda port: port.name)
        ],
        separators=(",", ":"),
    ).encode()
    return CompatibilityReport(
        package_id=package.package_id,
        compatible=not reasons,
        reasons=tuple(reasons),
        interface_digest=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )


def _validate_version(value: str) -> None:
    if not _VERSION.fullmatch(nonempty(value, "version")):
        raise ValueError("version must be semantic version syntax")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")
