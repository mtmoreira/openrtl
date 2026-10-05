# ADR 0028: durable batch delegation and explicit recovery

Status: local checkpoint `9abd92216d11d78f82b7505737015c61ece2e46a`.
45 focused tests, strict typing, full default validation and the production
canary passed. Live provider/container acceptance remains pending.

## Decision

Batch and interactive operation use one engineering state machine. Broad
acceptance is a typed, digest-reviewed authorization bound to a seed spec,
not an implicit provider permission or blanket acceptance of hidden defaults.
Scope controls assumption proposals, requirement changes and final acceptance
independently. Resolved questions retain their IDs as assumptions; every
assumption and scoped requirement change has a stable, reviewable warning.

Session v2 stores delegation, warning review state, authority and durable
ceilings. Calls are charged before invocation and never refunded on interruption
or revision. Repair count is likewise retained. Batch consumes steps before
execution and uses a persisted deadline across restarts. Reported acceptance
records whether it was individual or delegated; pending warnings are not
silently marked reviewed. Provider and simulation permissions are invocation
configuration, never restored from the ledger.

A lifetime OS writer lock complements SQLite compare-and-swap transactions.
Read-only inspection remains available. Legacy snapshots are readable and
explicitly migrated by appending a new snapshot, preserving historical bytes.

An active intent after interruption is not retried automatically. A new writer
can explicitly abandon its exact ID. Calls remain charged and unknown provider
outcomes are warned. Simulation cleanup requires the same explicitly selected
runtime and proves exact container name, operation label and local image against
a pre-create intent before removing that owned container. Partial artifacts
never count as a successful run. Legacy runtime intent gaps fail closed.

## Consequences and evidence boundary

There is no distributed execution or remote provider-job resumption. Sessions
are local Unix projects (fcntl/SQLite); destructive manual lock/database edits
are unsupported. Wall-clock deadlines assume a trustworthy host clock. Live
Docker/provider acceptance remains separate from provider-free orchestration
and reconciliation-double tests. No new production dependency, provider call,
image pull, remote operation or release is authorized by this implementation.
