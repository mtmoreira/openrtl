# ADR 0027: Executing, review-gated design sessions

Status: M36 implementation candidate; executable validation and live qualification pending.

## Decision

OpenRTL's conversational designer is an executing coordinator, not a list of
workflow labels. Requirements, assumptions, generated artifacts, approvals,
simulation receipts and signoff belong to a versioned engineering session.
SQLite transactions persist immutable snapshots, content-addressed artifacts
and allowlisted events. Raw chat, credentials and model reasoning are not an
event stream or shared expert memory. Durable structured specifications are
intentional user project content, not secret-safe storage for arbitrary input.

Each expert receives a versioned role-specific context pack. The reference
model derives from reviewed requirements; DV sees the model and verification
plan, not RTL text. Architecture, verification, model, RTL, assertion and DV
contributions have separate write scopes. These are independent invocations,
not a claim that using one model removes correlated mistakes.

Requirements and all proposed assumptions require digest-bound user approval.
Changing explanation detail never changes engineering authority. A successful
simulation proceeds to independent signoff and then final human acceptance.
A failed simulation can trigger a bounded RTL-only candidate repair and full
rerun. Experts cannot rewrite tests or requirements to pass this gate. Such
changes require an explicit new reviewed revision.

The first runtime composes AgentRig's existing tool-free structured generation
adapter, with late-bound explicitly named authentication, exact model identity,
timeouts, output bounds, no SDK retries, and invocation-scoped opt-in. OpenRTL
does not add provider lifecycle machinery to AgentRig or install a dependency.

Generated DV is executable untrusted code. Its runtime requires a user-selected
local container image ID and Unix socket, no image pull, no network, read-only
inputs, no host output/home/credential/socket mount, dropped capabilities and
bounded tmpfs/resources. Container removal targets only the exact returned
container ID. Source inputs and bounded evidence remain in the session.
There is no automatic unsandboxed execution fallback.

## Evidence and limitations

Unit doubles prove coordinator contracts only. Test receipts check XML test
identity, failures/skips, model tests and waveform artifacts; they are not
exhaustive coverage, formal proof, synthesis or PPA evidence. Untrusted tests
can still be inadequate; independent review and held-out evaluation are required.
Real provider generation and a real isolated ALU simulation are separate M36
qualification gates, not satisfied by provider-free tests or examples.

M36 persists an interrupted operation and refuses blind replay. Rich crash
reconciliation, batch delegation and assumption review are M37. General imports,
existing-design workflows, richer teaching/debug and held-out installed-release
qualification follow in M38–M40. These are not claimed complete by this ADR.
