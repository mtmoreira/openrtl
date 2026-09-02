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

Development `main` composes against the exact AgentRig 0.3.0 public contract.
The lockfile selects a sibling source checkout so local and CI validation can
bind that checkout to the published release commit. OpenRTL keeps provider
selection, credentials, RTL semantics, and review gates in its own layers; the
dependency upgrade grants no new runtime, tool, or provider authority.

Release-candidate qualification crosses a stricter package boundary: OpenRTL
is built once, AgentRig is installed only from its exact public annotated tag,
and the resulting wheel is exercised from a safely extracted examples archive
in an isolated environment. The qualification artifact binds both repository
identities and all distribution hashes. It is non-publishing and cannot imply
tag, release, provider, or deployment authority.

Published-release acceptance is version-specific and additive. The immutable
0.2.0 validator retains its AgentRig 0.2.2 contract. A separate 0.3.0 validator
verifies the public annotated OpenRTL and AgentRig tags, exact commits, manifest
and distribution bytes, then installs the public wheel and runs the extracted
examples in a fresh environment. The 0.3 lane includes the Verilator repair
walkthrough and its visibly distinct before/after waveform proof. Neither lane
uses the repository checkout as installed package state.

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

Provider-qualified application is another explicit boundary. A human approval
names the exact qualification identity and digest, proposal, ordered changes,
edit-plan digest, and review note. The adapter reconstructs the qualification,
planning report, and plan before delegating to the generic exact-byte engine.
Only a separate candidate may be written; a qualification-bound receipt records
the application while production RTL, provider access, and publication remain
unchanged.

Candidate promotion planning follows renewed simulation. The planner replays
the canonical qualification and application lineage, rehashes the candidate,
current target, comparison, evidence, results, and waveform, and emits one
`awaiting_promotion_approval` artifact. It is deliberately non-applying:
independent signoff and production-source replacement remain a separate gate.

Qualified candidate promotion is that separate applying gate. It requires
independent signoff over the exact canonical plan, target path and digest, and
candidate digest. The adapter rehashes both files, atomically replaces only the
named target with the exact candidate bytes, verifies the final digest, and
emits a promotion receipt. The intentionally broken regression source remains
separate from the promoted tracked target.

## Block-specific protocol diagnosis

The debug-session, evidence-anchor, observation, finding, proposal, and focus
contracts are shared across blocks. Protocol semantics are not. FIFO adapters
own occupancy, pointer, ordering, and full/empty checks; skid-buffer adapters
own transparent transfer, backpressure retention, ready/valid handshakes, and
same-edge dequeue/refill checks. Both produce the same reviewable artifact
types, so downstream review and evidence handling stay generic without
flattening distinct RTL protocols into one analyzer.

The ready/valid skid buffer is the second end-to-end example. Its broken
fixture differs from production only in refill readiness. Deterministic
simulation retains a failing trace where `s_ready` is low during a simultaneous
output opportunity and a passing trace where the replacement is accepted and
occupancy remains asserted. Repair output is non-applying and the production
source digest is checked before and after the case.

## Reuse and community boundary

A passing design produces a local package candidate containing interfaces,
parameters, clock/reset semantics, RTL/model/DV/assertions, dependencies,
provenance, license, and validation evidence. Compatibility is deterministic
and local. Remote publication is a separate adapter and approval boundary.

Verified simulation profiles are the reusable boundary between block-specific
simulation outputs and package candidacy. A checked-in profile binds design and
package identity, source files, interface, parameters, requirement set, test
identity, evidence schema/artifact keys, and waveform signals. The ingestion
adapter performs containment, regular-file, size, digest, passing-results, and
waveform-transition checks before normalizing either FIFO or skid-buffer
collateral into `VerifiedRunEvidence`. Package construction consumes only that
normalized evidence and the exact profile. The historical `verified-canary`
FIFO command remains a compatibility facade.
