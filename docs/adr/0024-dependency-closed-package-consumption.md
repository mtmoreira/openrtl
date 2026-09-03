# ADR 0024: Dependency-closed portable package consumption

## Status

Accepted.

## Context

Portable package bundles preserve one verified design and its evidence after
the producing worktree disappears. A package manifest can name exact package
dependencies, but consuming only the root would leave those dependencies
unresolved and would make the resulting workspace incomplete.

## Decision

Resolve a package closure from an explicit manifest-digest pin for the root and
every dependency. Load and verify every bundle before graph analysis. Require
each dependency package ID, version, and content digest to match its parent's
declaration. Reject duplicate package identities, missing or unused pins,
version or digest conflicts, and cycles.

Persist the deterministic dependency-first order in a canonical lock manifest.
Its separately retained file digest is the trust input for consumption.

Materialization reloads the lock by its expected digest, reverifies every
bundle, reconstructs the graph, and checks that the recorded order remains
valid. Each package is placed beneath its own package-ID directory, preventing
cross-package path collisions. The root interface and parameter requirements
are checked before the temporary workspace is atomically renamed into place.
Package sources and receipts are copied; package content is never executed.

## Consequences

- Downstream consumers receive a complete, reproducible package workspace.
- Transitive dependencies cannot be silently selected or substituted.
- Failure at any package leaves the final destination absent.
- Lock signing, remote catalog transport, and publication remain separate
  authorization boundaries.
