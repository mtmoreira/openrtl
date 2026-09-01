# OpenRTL release contract

## Current release: OpenRTL 0.3.0

OpenRTL 0.3.0 is the current published release of the simulation-first,
evidence-driven diagnosis and controlled repair workflow. Its annotated tag and
public artifacts are at
[the GitHub release](https://github.com/mtmoreira/openrtl/releases/tag/v0.3.0).
The release tag resolves to commit
`a69d27d645d351ade3a8974acf21c21b31c8dc5e`; its qualified package commit is
`6eedc375db42b99ea5ce38f150ead92599b259fd`.

The immutable release identity includes:

- `openrtl-0.3.0-py3-none-any.whl` — typed library and CLI;
- `openrtl-0.3.0.tar.gz` — source distribution;
- `openrtl-examples-0.3.0.tar.gz` — normalized runnable examples; and
- `openrtl-0.3.0-release.json` — qualified hashes and sizes.

The wheel pins `agentrig==0.3.0`. Public dependency acceptance requires the
annotated AgentRig tag `v0.3.0` to resolve to commit
`31b2ecae0605f0d6b63b5f060c929ca567ae16f2`.

## Public consumer acceptance

Validate OpenRTL 0.3.0 from public inputs only:

```sh
python tools/validate_public_release_v030.py \
  --output-directory build/public-release-v0.3.0-acceptance \
  --with-verilator
```

The validator downloads and verifies the exact manifest, wheel, source
distribution, and examples archive. It verifies both public annotated tags and
their commits, installs public AgentRig and the released OpenRTL wheel into a
fresh environment, safely extracts the examples, and runs the installed model,
fault diagnosis, repaired Verilator simulation, and semantic waveform proof.
The waveform lane must show FIFO level zero before repair and one after repair
at the same marker.

This lane performs public GitHub reads and local package installation. It makes
no provider call, resolves no credential, and performs no remote mutation.
Package-registry publication, signing, deployment, and provider access remain
outside the release acceptance contract.

## Historical release: OpenRTL 0.2.0

OpenRTL 0.2.0 remains an immutable historical release at
[its GitHub release](https://github.com/mtmoreira/openrtl/releases/tag/v0.2.0).
Its qualified package commit is
`83fb441a29dd655397fc6cfd7615538c0aecde5a`, and the annotated release tag
resolves to attestation commit `fe5f0db1604b1bd33b2b94107d5c3d1f603a1a1a`.
Its wheel pins `agentrig==0.2.2` and its acceptance lane remains fixed to public
AgentRig commit `b03087d1040b40e1d7d1efc98439d501964567c6`:

```sh
python tools/validate_public_release.py \
  --output-directory build/public-release-acceptance \
  --with-verilator
```

The v0.2 validator and tests are preserved separately rather than generalized
or rewritten for v0.3. This keeps previously published bytes and acceptance
semantics independently reproducible.

## Candidate preparation

Release candidates are built from one exact clean commit into an empty output
directory. `tools/validate_release_candidate.py` verifies deterministic wheel,
source, examples, and manifest artifacts, installs the exact public AgentRig
tag into a fresh environment, installs the candidate wheel without an editable
repository import, and runs `tools/verify_release_install.py` with explicit
OpenRTL and AgentRig versions.

Candidate qualification is local and non-publishing. Creating or pushing a
tag, creating a GitHub release, uploading assets, publishing to a package
registry, or deploying anything are separate remote effects requiring explicit
owner authorization.
