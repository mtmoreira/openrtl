"""Offline evaluation cases for orchestration and evidence discipline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from openrtl.domain import ExpertRole
from openrtl.domain._validation import identifier, nonempty


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    objective: str
    expected_roles: tuple[ExpertRole, ...]
    required_outputs: tuple[str, ...]
    must_escalate: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", identifier(self.case_id, "case_id"))
        object.__setattr__(self, "objective", nonempty(self.objective, "objective"))
        if not self.expected_roles or not self.required_outputs:
            raise ValueError("evaluation cases require roles and outputs")


def load_evaluation_cases(path: Path) -> tuple[EvaluationCase, ...]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("evaluation dataset must be a list")
    cases: list[EvaluationCase] = []
    for value in payload:
        if not isinstance(value, dict):
            raise ValueError("evaluation case must be an object")
        cases.append(
            EvaluationCase(
                case_id=str(value["case_id"]),
                objective=str(value["objective"]),
                expected_roles=tuple(ExpertRole(item) for item in value["expected_roles"]),
                required_outputs=tuple(str(item) for item in value["required_outputs"]),
                must_escalate=bool(value.get("must_escalate", False)),
            )
        )
    if len({value.case_id for value in cases}) != len(cases):
        raise ValueError("evaluation case IDs must be unique")
    return tuple(cases)
