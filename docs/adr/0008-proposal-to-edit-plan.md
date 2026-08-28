# ADR 0008: Qualify proposed edits before human approval

## Status

Accepted.

## Decision

OpenRTL converts an external, versioned exact-replacement specification into a
typed source-edit plan only after validating it against the retained repair
proposal, failed debug session, digest-pinned source, and proposal source
anchors. This planning operation is deterministic and non-applying. It emits a
separate planning report whose status is `awaiting_review` and whose digests
bind the proposal, debug session, edit specification, and complete edit plan.

The planner is generic: concrete RTL bytes remain in the external edit
specification and never enter Python strategy or application code. The planner
does not imply approval. Candidate application still requires a separate human
decision naming the proposal and exact change identities, approving the
canonical edit-plan digest, and recording a non-empty review note.

## Consequences

- generated or agent-authored edit specifications remain untrusted inputs;
- invalid changes, stale evidence, unanchored ranges, and unknown operations
  fail before a plan is offered for review;
- plan generation cannot write a candidate or production RTL;
- every changed specification byte changes the planning digest, while every
  semantic edit change also changes the canonical plan digest;
- future expert runtimes can produce the same edit-spec contract without
  expanding the trusted application engine.
