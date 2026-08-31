# ADR 0014: Candidate promotion planning

## Status

Accepted.

## Context

A provider-qualified repair can produce a separate candidate and renewed
simulation evidence without authorizing replacement of production RTL. Review
needs one compact artifact that binds the exact candidate, current target,
qualification, application receipts, comparison, results, and waveform before
any production-source decision is requested.

## Decision

Add a deterministic, non-applying candidate-promotion planner. It reconstructs
the canonical qualification and both application receipts, rehashes every
selected artifact, requires a failing-before/passing-after visibly distinct
comparison, and binds the candidate and current target as different immutable
byte sets. The resulting plan is `awaiting_promotion_approval` and names a
future explicit production-promotion operation.

The planner never writes the target, invokes a provider, resolves credentials,
launches a GUI, or performs a remote operation. Approval and production-source
replacement remain a separate future boundary.

## Consequences

- Reviewers receive one canonical digest covering the full promotion lineage.
- Stale target, candidate, receipt, comparison, results, waveform, or evidence
  bytes fail closed before a plan is emitted.
- A validated candidate is still not production RTL; promotion requires an
  independent signoff and an explicitly selected applying operation.
