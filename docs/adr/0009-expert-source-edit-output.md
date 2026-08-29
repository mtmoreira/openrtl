# ADR 0009: Treat expert source edits as untrusted typed output

## Status

Accepted.

## Decision

OpenRTL prepares a provider-neutral request for the Diagnosis and Closure
Engineer that binds an exact context pack, repair proposal, failed debug
session, source digest, and complete ordered change set. The request declares a
strict versioned output schema and the only allowlisted source operation. It
does not select or invoke a provider.

Expert output must repeat every request binding and cover every requested
change with uniquely identified exact-byte replacements. OpenRTL rejects extra
fields, stale or mismatched bindings, unknown operations, incomplete coverage,
and noncanonical context or request identities. Accepted output remains
untrusted and non-applying. It produces an external
`openrtl.source-edit-spec.v1` artifact and an `awaiting_qualification` report.

The existing deterministic planner is the next gate. It independently checks
the specification against the proposal, failed session, source anchors, and
actual source bytes before producing an `awaiting_review` edit plan. Human
approval of that exact plan digest remains a separate prerequisite for writing
a candidate.

## Consequences

- model and provider selection remain outside the hardware artifact contract;
- provider payloads, hidden reasoning, and session history are not persisted;
- concrete RTL replacement bytes live only in external expert/specification
  artifacts, never in Python strategy code;
- accepting expert output cannot qualify, approve, apply, or promote an edit;
- provider-free tests can exercise the complete contract with a synthetic
  strict response and no network call.
