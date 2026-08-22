"""RTL design, interface, plan, and traceability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain._validation import identifier, nonempty
from openrtl.domain.artifacts import ArtifactRef


class RequirementPriority(str, Enum):
    MUST = "must"
    SHOULD = "should"
    MAY = "may"


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    statement: str
    acceptance: str
    priority: RequirementPriority = RequirementPriority.MUST

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_id", identifier(self.requirement_id, "requirement_id"))
        object.__setattr__(self, "statement", nonempty(self.statement, "statement"))
        object.__setattr__(self, "acceptance", nonempty(self.acceptance, "acceptance"))


class PortDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


@dataclass(frozen=True, order=True)
class InterfacePort:
    name: str
    direction: PortDirection
    width: int
    clock_domain: str | None = None
    signed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", identifier(self.name, "port name"))
        if self.width < 1:
            raise ValueError("port width must be positive")
        if self.clock_domain is not None:
            object.__setattr__(
                self,
                "clock_domain",
                identifier(self.clock_domain, "clock_domain"),
            )


@dataclass(frozen=True, order=True)
class Parameter:
    name: str
    default: int | bool | str
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", identifier(self.name, "parameter name"))
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum cannot exceed maximum")
        if isinstance(self.default, int) and not isinstance(self.default, bool):
            if self.minimum is not None and self.default < self.minimum:
                raise ValueError("parameter default is below minimum")
            if self.maximum is not None and self.default > self.maximum:
                raise ValueError("parameter default is above maximum")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("only integer parameters may declare numeric bounds")


@dataclass(frozen=True)
class ClockResetContract:
    clock: str
    reset: str
    reset_active_low: bool
    reset_async: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "clock", identifier(self.clock, "clock"))
        object.__setattr__(self, "reset", identifier(self.reset, "reset"))


@dataclass(frozen=True)
class DesignSpecification:
    design_id: str
    title: str
    summary: str
    requirements: tuple[Requirement, ...]
    ports: tuple[InterfacePort, ...]
    parameters: tuple[Parameter, ...]
    clock_resets: tuple[ClockResetContract, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "design_id", identifier(self.design_id, "design_id"))
        object.__setattr__(self, "title", nonempty(self.title, "title"))
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))
        requirements = tuple(self.requirements)
        ports = tuple(self.ports)
        parameters = tuple(self.parameters)
        clock_resets = tuple(self.clock_resets)
        if not requirements:
            raise ValueError("a design must have requirements")
        _unique(tuple(value.requirement_id for value in requirements), "requirement IDs")
        _unique(tuple(value.name for value in ports), "port names")
        _unique(tuple(value.name for value in parameters), "parameter names")
        _unique(tuple(value.clock for value in clock_resets), "clock domains")
        port_names = {value.name for value in ports}
        for clock_reset in clock_resets:
            if clock_reset.clock not in port_names or clock_reset.reset not in port_names:
                raise ValueError("clock/reset contracts must reference interface ports")
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "ports", ports)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "clock_resets", clock_resets)


class PlanKind(str, Enum):
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    TEST = "test"
    INTEGRATION = "integration"


@dataclass(frozen=True)
class PlanItem:
    item_id: str
    kind: PlanKind
    objective: str
    requirement_ids: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", identifier(self.item_id, "item_id"))
        object.__setattr__(self, "objective", nonempty(self.objective, "objective"))
        requirements = tuple(identifier(value, "requirement_id") for value in self.requirement_ids)
        dependencies = tuple(identifier(value, "depends_on") for value in self.depends_on)
        if not requirements or len(set(requirements)) != len(requirements):
            raise ValueError("requirement_ids must be non-empty and unique")
        if len(set(dependencies)) != len(dependencies) or self.item_id in dependencies:
            raise ValueError("plan dependencies must be unique and cannot reference the item itself")
        object.__setattr__(self, "requirement_ids", requirements)
        object.__setattr__(self, "depends_on", dependencies)


@dataclass(frozen=True)
class TraceLink:
    requirement_id: str
    artifact_refs: tuple[ArtifactRef, ...]
    plan_item_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_id", identifier(self.requirement_id, "requirement_id"))
        refs = tuple(self.artifact_refs)
        plans = tuple(identifier(value, "plan_item_id") for value in self.plan_item_ids)
        evidence = tuple(identifier(value, "evidence_id") for value in self.evidence_ids)
        if len(set(refs)) != len(refs):
            raise ValueError("artifact_refs must be unique")
        _unique(plans, "plan item IDs")
        _unique(evidence, "evidence IDs")
        object.__setattr__(self, "artifact_refs", refs)
        object.__setattr__(self, "plan_item_ids", plans)
        object.__setattr__(self, "evidence_ids", evidence)


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")
