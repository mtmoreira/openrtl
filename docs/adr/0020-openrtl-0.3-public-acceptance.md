# ADR 0020: Add versioned public acceptance for OpenRTL 0.3

## Status

Accepted.

## Context

OpenRTL 0.3.0 and AgentRig 0.3.0 are published as annotated Git tags with an
OpenRTL wheel, source distribution, examples archive, and release manifest.
The existing public validator is an immutable OpenRTL 0.2.0 contract tied to
AgentRig 0.2.2. Reparameterizing it would weaken the ability to reproduce the
historical release and could silently replace one acceptance contract with
another.

## Decision

Keep the 0.2.0 validator and tests unchanged. Add a dedicated 0.3.0 validator
that fails closed unless:

- the public OpenRTL and AgentRig annotated tags resolve to their exact release
  commits;
- the downloaded manifest and all three distribution artifacts match their
  qualified hashes and sizes;
- an isolated environment reports OpenRTL 0.3.0 and AgentRig 0.3.0; and
- the safely extracted examples pass their model, diagnosis, repaired
  Verilator simulation, and visibly distinct waveform checks.

Run both release lanes as independent CI jobs. Provider calls, credential
resolution, registry publication, signing, and deployment are outside both
acceptance contracts.

## Consequences

Each published release has explicit executable evidence without mutating the
meaning of older evidence. A new release requires a new immutable acceptance
contract or a reviewed generic scheme that preserves all prior identities.
The lane performs public reads and local package installation, so it is slower
than default provider-free repository validation and remains separately
selectable.
