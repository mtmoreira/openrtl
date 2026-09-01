# ADR 0019: Qualify OpenRTL 0.3 from exact public dependency source

- Status: accepted
- Date: 2026-08-31

## Context

OpenRTL 0.3.0 pins AgentRig 0.3.0. Editable sibling validation proves source
compatibility but cannot prove that a consumer can reproduce the package from
public inputs. The published OpenRTL 0.2.0 acceptance lane is an immutable
historical contract and must not be repurposed for a new release line.

## Decision

Build the OpenRTL 0.3.0 wheel, source distribution, and normalized examples
archive from one exact clean commit. Validate their manifests and metadata,
including the exact `agentrig==0.3.0` dependency. Install AgentRig only from
public repository tag `v0.3.0`, after verifying the tag and checkout resolve to
commit `31b2ecae0605f0d6b63b5f060c929ca567ae16f2`.

Install the OpenRTL wheel without the repository checkout, safely extract the
examples archive, and run the model, diagnosis, and Verilator repair examples.
The repair lane must prove visibly distinct waveforms: FIFO level is zero in
the faulty trace and one in the repaired trace at the same marker. Persist a
qualification document binding the artifact hashes, manifest hash, OpenRTL
commit, and public AgentRig identity.

## Consequences

The candidate can be independently reviewed and reproduced without trusting a
local sibling checkout. The OpenRTL 0.2.0 public acceptance code and artifacts
remain unchanged. Candidate qualification creates no OpenRTL tag, GitHub
Release, upload, provider call, or deployment; each remote effect requires
separate explicit authorization.
