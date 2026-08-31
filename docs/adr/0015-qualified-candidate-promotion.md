# ADR 0015: Qualified candidate promotion

## Status

Accepted.

## Context

A provider-qualified candidate can pass renewed simulation while remaining
separate from its reviewed target. M22 binds that lineage into a canonical,
non-applying promotion plan. Applying the candidate requires an independently
reviewable operation that cannot silently substitute a different plan, target,
candidate, or signoff.

## Decision

Add a generic production-promotion adapter and CLI command. The command
reconstructs the canonical M22 plan, requires independent signoff bound to the
exact plan ID and digest, target path and digest, candidate digest, and bounded
note, then rehashes the selected files. Only when every binding matches does it
atomically replace the target with the exact candidate bytes and emit a
content-addressed promotion receipt.

The old intentionally broken FIFO behavior moves to a separately named
regression fixture before the approved tracked target is promoted. Concrete RTL
replacement bytes remain outside Python. Provider calls, credential resolution,
remote operations, and changes to `examples/fifo/rtl/sync_fifo.sv` are outside
this operation.

## Consequences

- A successful receipt proves the final target digest equals the approved
  candidate digest.
- Stale plans, candidates, targets, path substitutions, symlinks, and partial
  approvals fail before the target write.
- The diagnostic and provider-free qualification pipeline remains reproducible
  through the explicit regression fixture.
- Promotion is a local source mutation; publication remains separately
  authorized.
