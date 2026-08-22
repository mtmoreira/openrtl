"""Explicit runtime, model, tool, and expert-role selection."""

from __future__ import annotations

from dataclasses import dataclass

from openrtl.domain._validation import identifier, nonempty
from openrtl.domain.context import ExpertRole


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    provider: str
    model: str
    binding_id: str
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "provider", identifier(self.provider, "provider"))
        object.__setattr__(self, "model", nonempty(self.model, "model"))
        object.__setattr__(self, "binding_id", identifier(self.binding_id, "binding_id"))
        features = tuple(identifier(value, "feature") for value in self.features)
        if len(set(features)) != len(features):
            raise ValueError("features must be unique")
        object.__setattr__(self, "features", features)


@dataclass(frozen=True)
class ToolProfile:
    profile_id: str
    tool_ids: tuple[str, ...]
    simulator: str | None = None
    waveform_viewer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", identifier(self.profile_id, "profile_id"))
        tools = tuple(identifier(value, "tool_id") for value in self.tool_ids)
        if len(set(tools)) != len(tools):
            raise ValueError("tool_ids must be unique")
        if self.simulator is not None:
            object.__setattr__(self, "simulator", identifier(self.simulator, "simulator"))
        if self.waveform_viewer is not None:
            object.__setattr__(
                self,
                "waveform_viewer",
                identifier(self.waveform_viewer, "waveform_viewer"),
            )
        object.__setattr__(self, "tool_ids", tools)


@dataclass(frozen=True)
class ExpertBinding:
    role: ExpertRole
    runtime_profile_id: str
    tool_profile_id: str
    max_turns: int = 8
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_profile_id",
            identifier(self.runtime_profile_id, "runtime_profile_id"),
        )
        object.__setattr__(
            self,
            "tool_profile_id",
            identifier(self.tool_profile_id, "tool_profile_id"),
        )
        if self.max_turns < 1 or self.timeout_seconds < 1:
            raise ValueError("expert execution bounds must be positive")


@dataclass(frozen=True)
class ProjectProfile:
    profile_id: str
    runtimes: tuple[RuntimeProfile, ...]
    tool_profiles: tuple[ToolProfile, ...]
    experts: tuple[ExpertBinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", identifier(self.profile_id, "profile_id"))
        runtimes = tuple(self.runtimes)
        tools = tuple(self.tool_profiles)
        experts = tuple(self.experts)
        _require_unique(tuple(value.profile_id for value in runtimes), "runtime profile IDs")
        _require_unique(tuple(value.profile_id for value in tools), "tool profile IDs")
        _require_unique(tuple(value.role.value for value in experts), "expert roles")
        runtime_ids = {value.profile_id for value in runtimes}
        tool_ids = {value.profile_id for value in tools}
        for expert in experts:
            if expert.runtime_profile_id not in runtime_ids:
                raise ValueError(f"unknown runtime profile: {expert.runtime_profile_id}")
            if expert.tool_profile_id not in tool_ids:
                raise ValueError(f"unknown tool profile: {expert.tool_profile_id}")
        object.__setattr__(self, "runtimes", runtimes)
        object.__setattr__(self, "tool_profiles", tools)
        object.__setattr__(self, "experts", experts)

    def expert(self, role: ExpertRole) -> ExpertBinding:
        for binding in self.experts:
            if binding.role is role:
                return binding
        raise KeyError(f"no expert binding for role: {role.value}")


def _require_unique(values: tuple[str, ...], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")
