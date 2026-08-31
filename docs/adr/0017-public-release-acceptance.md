# ADR 0017: Verify releases from the public consumer boundary

## Status

Accepted.

## Decision

Every published OpenRTL release has a public-consumer acceptance lane that
starts without repository-built packages. The lane downloads the immutable
release manifest and all declared OpenRTL artifacts from the public GitHub
release, checks the manifest against its previously qualified digest, and
checks every artifact's exact size and SHA-256 digest.

OpenRTL 0.2.0 depends on AgentRig 0.2.2. Until that Python distribution has an
immutable package release of its own, the public lane obtains it by checking
out exact public AgentRig commit
`b03087d1040b40e1d7d1efc98439d501964567c6`. The checkout commit and installed
distribution version must both match. The lane never substitutes current
AgentRig `main` or an unpinned registry package.

The lane creates an isolated environment, removes inherited Python import
paths, installs AgentRig from that exact public checkout, installs the
downloaded OpenRTL wheel without dependency substitution, safely extracts the
downloaded examples archive, and executes its bundled verifier. The complete
lane includes the model, fault diagnosis, and Verilator repair walkthrough.

## Consequences

- Public installability is tested independently of the release-producing
  checkout and retained local build directory.
- A mutable release asset, redirect outside trusted GitHub hosts, dependency
  drift, unsafe archive member, wrong installed version, or failed example
  causes acceptance to fail closed.
- The GitHub-hosted lane requires only public network reads. It performs no
  provider call, credential resolution, publication, deployment, or source
  mutation.
- Publishing AgentRig as an immutable Python artifact can later replace the
  exact-source checkout, but that changes the acceptance contract and requires
  a new reviewed decision.
