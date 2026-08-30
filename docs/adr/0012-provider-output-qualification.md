# ADR 0012: Bind provider output lineage before review

## Status

Accepted.

## Context

The explicit provider lane stops at an untrusted source-edit specification and
two `awaiting_qualification` lifecycle reports. Qualifying only the edit-spec
file would prove its source anchors and exact replacement bytes, but would not
prove that it came from the reviewed provider plan and the recorded one-call
execution. Independently valid artifacts from different runs could otherwise
be mixed before review.

## Decision

Add a deterministic, provider-free qualification command that requires the
exact provider plan, provider execution report, invocation report, suggestion
report, edit specification, proposal, failed debug session, and source. It
reconstructs the canonical lifecycle contracts, verifies every digest and
identity edge, drafts the typed edit plan, and emits a content-addressed
`openrtl.provider-output-qualification.v1` receipt.

The receipt remains non-applying and `awaiting_review`. Provider output remains
untrusted; the receipt proves provenance and deterministic qualification, not
correctness or approval. Human review and exact edit-plan approval remain a
separate gate.

## Consequences

- cross-run artifact mixing, stale source, altered edit bytes, runtime drift,
  and lifecycle-report tampering fail before a review receipt is written;
- qualification reads no credential, makes no provider or network call, and
  cannot write a candidate RTL file;
- generic provider-free edit specifications can still use
  `repair draft-source-edits`; provider-produced specifications use the
  provenance-bound qualification command;
- no raw prompt, credential, hidden reasoning, or provider payload is added to
  the qualification receipt.
