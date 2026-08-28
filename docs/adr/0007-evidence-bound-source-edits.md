# ADR 0007: Bind repair approval to typed source edits

## Status

Accepted. This decision generalizes and supersedes the FIFO-specific
transformation mechanism in ADR 0006; its candidate-only and validation
boundaries remain in force.

## Decision

Concrete repair instructions live in a versioned `source-edit-plan` artifact,
not in the Python application engine. A plan identifies the proposal, failing
debug session, digest-pinned source, and one or more non-overlapping edits.
The first allowlisted operation is `replace_exact_bytes`; each edit records its
byte range, expected bytes, replacement bytes, and both content digests.

Review approval binds the canonical SHA-256 digest of the complete edit plan
in addition to the proposal and change identities. The generic application
engine rejects unknown fields or operations, stale source or evidence,
unanchored ranges, overlapping edits, unsafe paths, and mismatched approval.
It writes only a separate candidate and records the plan, edits, changed lines,
and before/after digests in its application report.

The FIFO level-update text is retained as a reviewable example JSON
specification. The deterministic example turns that untrusted specification
into a fully digest-bound edit plan before approval and application. Future
planners may produce the same artifact contract without expanding the trusted
application engine or executing generated code.

ADR 0008 implements that planner boundary and makes its pre-approval state and
input/output digest linkage explicit.

## Consequences

- Python application code has no FIFO statement or replacement knowledge;
- moving repair text into an artifact does not make it trusted automatically;
- approval changes whenever any edit byte, range, identity, or source changes;
- the initial operation remains intentionally narrow and auditable;
- syntax-aware edit operations may be added later only as separately reviewed
  allowlisted adapters.
