# ADR 0022: Profile-driven verified simulation packages

## Status

Accepted.

## Context

The original package-candidacy path ingested only the FIFO canary and assembled
its package in a FIFO-specific scripted workflow. The skid buffer proves a
second protocol, but its passing evidence uses a comparison-oriented manifest.
Copying package construction into another block-specific Python path would make
each future example a new integration rather than a consumer of one contract.

## Decision

Define a checked-in `openrtl.verified-simulation-profile.v1` document for each
design. The profile owns design and package identity, files, interface,
parameters, requirements, passing test identity, evidence schema selectors,
and waveform focus signals. A generic adapter verifies the profile and retained
manifest, normalizes the selected artifacts into `VerifiedRunEvidence`, and
constructs a simulation-verified `DesignPackage` suitable for the local
catalog.

Evidence schemas remain explicit adapters. FIFO retains its historical canary
schema and command. The skid profile selects the passing production side of its
paired fault/repair evidence. Both paths require contained regular files,
exact hashes and sizes, one expected passing testcase, the declared RTL source,
the exact requirement set, and transitions for every declared focus signal.
Unknown schemas and mixed profile, source, manifest, or waveform states fail
closed.

## Consequences

- Package descriptions and block identities are reviewable data rather than
  Python branches.
- FIFO and skid-buffer candidates share normalization and local catalog flow.
- Protocol diagnosis stays block-specific and is not flattened into the
  package boundary.
- Local catalog insertion refuses replacement of an existing version.
- Remote catalog publication remains deferred and separately authorized.
