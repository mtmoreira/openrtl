"""Bounded repair and escalation policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain._validation import identifier, nonempty


@dataclass(frozen=True)
class ClosurePolicy:
    max_attempts: int = 8
    max_equivalent_failures: int = 3
    max_no_progress_cycles: int = 2

    def __post_init__(self) -> None:
        if min(self.max_attempts, self.max_equivalent_failures, self.max_no_progress_cycles) < 1:
            raise ValueError("closure limits must be positive")
        if self.max_equivalent_failures > self.max_attempts:
            raise ValueError("equivalent failure limit cannot exceed max attempts")


@dataclass(frozen=True)
class RepairAttempt:
    attempt: int
    failure_signature: str
    hypothesis: str
    evidence_score: int

    def __post_init__(self) -> None:
        if self.attempt < 1 or self.evidence_score < 0:
            raise ValueError("attempt and evidence_score must be non-negative with a positive attempt")
        object.__setattr__(
            self,
            "failure_signature",
            identifier(self.failure_signature, "failure_signature"),
        )
        object.__setattr__(self, "hypothesis", nonempty(self.hypothesis, "hypothesis"))


class ClosureDecision(str, Enum):
    CONTINUE = "continue"
    PASSED = "passed"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class EscalationReport:
    reason: str
    failure_signature: str
    hypotheses: tuple[str, ...]
    attempt_count: int
    best_evidence_score: int
    requested_decision: str


class ClosureTracker:
    def __init__(self, policy: ClosurePolicy) -> None:
        self.policy = policy
        self._attempts: list[RepairAttempt] = []

    def record(self, attempt: RepairAttempt) -> None:
        if attempt.attempt != len(self._attempts) + 1:
            raise ValueError("repair attempts must be contiguous")
        self._attempts.append(attempt)

    def decide(self, passed: bool = False) -> tuple[ClosureDecision, EscalationReport | None]:
        if passed:
            return ClosureDecision.PASSED, None
        if not self._attempts:
            return ClosureDecision.CONTINUE, None
        latest = self._attempts[-1]
        same_signature = sum(
            attempt.failure_signature == latest.failure_signature for attempt in self._attempts
        )
        reason: str | None = None
        if len(self._attempts) >= self.policy.max_attempts:
            reason = "attempt_budget_exhausted"
        elif same_signature >= self.policy.max_equivalent_failures:
            reason = "equivalent_failure_limit"
        elif self._no_progress_cycles() >= self.policy.max_no_progress_cycles:
            reason = "no_evidence_progress"
        if reason is None:
            return ClosureDecision.CONTINUE, None
        return (
            ClosureDecision.ESCALATE,
            EscalationReport(
                reason=reason,
                failure_signature=latest.failure_signature,
                hypotheses=tuple(attempt.hypothesis for attempt in self._attempts),
                attempt_count=len(self._attempts),
                best_evidence_score=max(attempt.evidence_score for attempt in self._attempts),
                requested_decision="Clarify the requirement or select a new repair strategy.",
            ),
        )

    def _no_progress_cycles(self) -> int:
        if len(self._attempts) < 2:
            return 0
        best = self._attempts[0].evidence_score
        no_progress = 0
        for attempt in self._attempts[1:]:
            if attempt.evidence_score > best:
                best = attempt.evidence_score
                no_progress = 0
            else:
                no_progress += 1
        return no_progress
