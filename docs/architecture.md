# OpenRTL architecture

## Shared context is an artifact graph

OpenRTL does not give a team of agents an opaque shared chat transcript. The
project knowledge base stores immutable artifact revisions, decisions,
assumptions, evidence anchors, run bundles, and dependency edges. A context-pack
builder selects the exact revisions and excerpts required for one expert role.
The pack identity and source digests make an invocation reproducible.

An expert returns a structured contribution. Validation decides whether the
contribution becomes a new artifact revision. Provider session continuation may
improve interaction, but it is never the source of truth.

## Layers

1. `openrtl.domain`: requirements, design state, artifacts, evidence, packages,
   expert profiles, and convergence policy.
2. `openrtl.application`: context construction, expert coordination, workflow
   state transitions, teaching, reviews, diagnosis, and reuse planning.
3. `openrtl.adapters`: filesystem persistence, EDA processes, log/trace
   ingestion, AgentRig composition, and waveform-viewer requests.
4. `openrtl.cli`: a local conversation and deterministic developer front door.

Domain modules do not import adapters. AgentRig types appear at the application
composition boundary, not inside hardware-design value objects.

## Expert roles

- Design Lead
- Learning Coach
- Design Architect
- Reuse and Integration Architect
- Reference Model Engineer
- Verification Architect
- RTL Engineer
- Assertion Engineer
- DV Engineer
- Diagnosis and Closure Engineer
- Independent Signoff Reviewer

Roles are stable contracts, not permanently running agents. The Design Lead
selects only the roles required for a workflow stage.

## Evidence anchors

- requirement: a stable requirement identifier;
- source: repository-relative path, line, and content digest;
- log: run and event identifiers;
- waveform: trace, time interval, signals, and markers;
- package: immutable design-package identity and version.

Review output cites anchors rather than copying large mutable payloads.

## Tool and runtime selection

An expert binding selects one AgentRig runtime binding plus a bound toolset.
Preflight verifies the runtime supports every required feature and tool. CLI
tools are exact guarded processes. MCP bindings declare server identity,
transport, allowlists, trust, and retention. Missing support is a normalized
blocked result, never a silent substitution.

## Simulation and deferred backends

V1 implements a `SimulationBackend` using Verilator and cocotb. Waveforms are
VCD and viewer focus requests target Surfer. Inspection produces bounded JSON;
focus generation produces deterministic command files; and GUI launch crosses
an explicit, non-repeatable AgentRig tool boundary. Formal, synthesis,
implementation, and device programming remain future backends that can
contribute evidence to the same graph without changing logical design identity.

Waveform diagnosis is separate from GUI navigation. A block-specific adapter
samples pre-edge inputs and post-edge state from the bounded VCD index and emits
generic application-layer debug observations and findings. Each session binds
the trace digest, requirement IDs, clock-edge markers, and relevant RTL source
lines. Findings recommend bounded next probes but never mutate RTL directly.

A failed debug session can become a digest-bound context item for the
Diagnosis and Closure Engineer. Generic application contracts require every
repair change to cover explicit findings and matching requirements using only
the session's source and waveform anchors. Block-specific adapters may propose
bounded strategies, but proposals are non-applying review artifacts; RTL edits
remain a later reviewed workflow action followed by deterministic validation.

Reviewed repair application is a separate candidate-only boundary. A typed
source-edit plan carries concrete expected and replacement bytes, exact ranges,
and source digests. Approval binds the canonical plan digest plus the proposal
and change identities. The generic engine knows only allowlisted edit
operations and reverifies debug-session, source-anchor, path, range, and digest
linkage before writing a separate candidate. Verilator qualification retains
the plan, failing and repaired waveforms, and a hash-bound comparison;
production promotion remains outside this boundary.

Plan qualification precedes that approval boundary. A generic, non-applying
planner converts an external exact-replacement specification into the typed
plan only after checking the proposal, failed session, source digest, change
identities, and anchored byte ranges. Its separate `awaiting_review` report
binds all input and output digests; it neither approves nor applies the plan.

Expert suggestion precedes plan qualification. OpenRTL prepares a
provider-neutral request for the Diagnosis and Closure Engineer with an exact
role-specific context pack and proposal, failed-session, source, and change
bindings. Strict expert output is ingested as an untrusted external
specification only when every binding and requested change is repeated in
canonical form. Its report remains `awaiting_qualification`; it cannot invoke a
provider, approve a plan, or write RTL. Deterministic qualification and human
review remain the two separate downstream gates.

Controlled expert invocation precedes suggestion ingestion. OpenRTL gives an
AgentRig structured generator one canonical envelope containing only bounded
proposal data, linked waveform observations, and digest-bound source excerpts.
The caller selects exact runtime binding, capability, provider, model,
retention, timeout, input/output bytes, and output tokens. The lane exposes no
tools and permits one turn. Its safe report records identities, aggregate usage,
and canonical digests, never hidden reasoning or raw provider payloads. The
default CLI implementation is scripted and provider-free. The OpenAI Responses
adapter is a separate two-step boundary: a non-executing canonical plan records
the exact runtime and a credential-environment name, then the provider-specific
command requires both `--with-openai-provider` and that plan's exact digest.
Credential resolution and short-lived client creation occur only after every
local preflight passes. Its value-safe execution report remains
`awaiting_qualification`; no provider output can qualify or apply RTL.

Provider-output qualification is a separate deterministic adapter. It
reconstructs the reviewed provider plan, execution receipt, invocation report,
and suggestion report; binds them to the exact edit-spec file, proposal,
failed session, and source; and only then produces the typed edit plan plus a
content-addressed qualification receipt. The receipt is `awaiting_review`,
keeps provider output untrusted, applies no changes, reads no credential, and
makes no provider call.

## Reuse and community boundary

A passing design produces a local package candidate containing interfaces,
parameters, clock/reset semantics, RTL/model/DV/assertions, dependencies,
provenance, license, and validation evidence. Compatibility is deterministic
and local. Remote publication is a separate adapter and approval boundary.
