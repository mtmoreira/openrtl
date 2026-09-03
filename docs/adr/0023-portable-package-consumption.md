# ADR 0023: Digest-bound portable package consumption

## Status

Accepted.

## Context

Verified simulation profiles produce trustworthy local package candidates, but
the initial catalog manifest points at files in the producing repository and
does not contain enough interface data to reconstruct a `DesignPackage`.
Retained milestone evidence proves those bytes existed, yet a downstream
consumer cannot safely reload or materialize the candidate after its worktree
has been removed.

## Decision

Add a self-contained portable bundle alongside the lightweight local catalog.
The bundle copies package sources plus the exact simulation profile, evidence
manifest, log, results, and waveform that qualified the candidate. Its manifest
records the complete typed package, original and bundle-relative paths, hashes,
sizes, evidence identifiers, and dependencies.

Loading requires a separately supplied expected manifest digest. The loader
rejects unknown fields, identity drift, missing files, symlinks, size or digest
changes, incomplete supporting evidence, and a reconstructed package digest
that differs from the reviewed candidate.

Materialization performs deterministic port and parameter compatibility before
writing. It creates a fresh temporary sibling, copies only verified package
source bytes, writes a receipt binding the package and bundle digests, and
renames the result atomically. Package contents and install hooks are never
executed.

## Consequences

- FIFO and skid-buffer packages remain consumable without their producing
  worktrees.
- The expected bundle digest becomes the explicit trust input for local reuse.
- Evidence travels with the candidate for audit but is not placed in the
  consumer source tree.
- Existing destinations and duplicate catalog versions fail closed.
- Remote distribution, signing, and publication remain separate future
  authorization boundaries.
