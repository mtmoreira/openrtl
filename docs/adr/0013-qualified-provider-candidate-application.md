# ADR 0013: Qualification-bound provider repair application

## Status

Accepted.

## Context

Provider-output qualification proves provenance and produces a deterministic
typed edit plan, but its `awaiting_review` receipt must not itself authorize a
write. The existing generic repair approval binds proposals, changes, and edit
plans, but does not prove that a provider-produced plan is the exact plan named
by a reviewed provider qualification.

## Decision

Add a separate provider-qualified application boundary. Human approval binds
the qualification identity and digest, proposal identity, ordered changes,
edit-plan digest, and a bounded review note. The adapter reconstructs the
canonical qualification, planning report, and edit plan; rejects stale,
cross-run, or non-canonical artifacts; and only then delegates to the generic
candidate-only edit engine.

The adapter writes only a separate candidate. It emits both the generic
application report and a qualification-bound receipt. It never edits the input
RTL, trusts provider output, invokes a provider, resolves credentials, or
authorizes publication.

## Consequences

- Provider-produced repairs require one additional explicit digest approval.
- Non-provider edit plans retain the existing generic reviewed application path.
- Candidate simulation and visibly distinct before/after waveforms remain
  mandatory evidence before any later production-source decision.
