# ADR 0010: Invoke repair experts through a bounded structured-generation lane

## Status

Accepted.

## Decision

OpenRTL composes the M17 expert source-edit contract with AgentRig's portable
structured-generation capability. One invocation carries a canonical bounded
envelope containing only the reviewed request bindings, selected repair
changes, linked waveform findings and observations, and digest-bound source
excerpts. The invocation is one turn, tool-free, deadline-bound, output-token
and byte-bound, and selected by exact runtime binding, capability, provider,
model, and data-retention policy.

The generator must return the strict `openrtl.expert-source-edit-output.v1`
schema. OpenRTL independently revalidates every M17 binding and operation,
rejects incomplete or extra output, verifies the result model identity, and
persists only the canonical envelope, normalized response, aggregate usage,
safe runtime identity, and digests. Hidden reasoning, provider session state,
raw transport errors, and tool requests are outside the contract.

Validation and the public CLI expose only a scripted, provider-free generator.
A live provider adapter may be composed later only through a separate explicit
opt-in boundary; there is no implicit provider fallback or credential lookup.

## Consequences

- a successful invocation still produces only an untrusted
  `awaiting_qualification` suggestion;
- invocation cannot qualify, approve, apply, or promote source edits;
- stale evidence, capability drift, retention drift, model drift, tool-enabled
  generators, truncation, oversized output, and schema additions fail closed;
- deterministic tests exercise the complete orchestration without network,
  credentials, provider SDKs, GUI launch, or RTL mutation.
