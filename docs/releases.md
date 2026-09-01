# OpenRTL release contract

OpenRTL 0.2.0 is the first published release of the complete V1 simulation and
evidence-driven repair workflow. Its annotated tag and public artifacts are at
[the GitHub release](https://github.com/mtmoreira/openrtl/releases/tag/v0.2.0).
The immutable release identity is one semantic version, one exact qualified
commit, and these artifacts:

- `openrtl-0.2.0-py3-none-any.whl` — the typed OpenRTL library and CLI;
- `openrtl-0.2.0.tar.gz` — source distribution including examples;
- `openrtl-examples-0.2.0.tar.gz` — normalized runnable examples; and
- `openrtl-0.2.0-release.json` — hashes and sizes for the preceding artifacts.

The wheel retains the exact base dependency `agentrig==0.2.2`; provider SDKs
remain opt-in and are not needed by the provider-free verification lane.

## Development after 0.2.0

Development `main` is now the OpenRTL 0.3.0 line and declares
`agentrig==0.3.0`. Local and CI validation bind the editable sibling checkout
to published AgentRig commit
`31b2ecae0605f0d6b63b5f060c929ca567ae16f2`. The migration preserves the
existing command, MCP, structured-generation, and OpenAI Responses boundaries;
it does not enable a provider or inherit AgentRig's new image runtime
implicitly.

This source-version change does not create an OpenRTL 0.3.0 release. The
published 0.2.0 tag, artifacts, dependency pin, and public-only acceptance
validator below remain immutable historical contracts. A future 0.3.0 package
candidate, tag, or publication requires its own qualification and explicit
owner authorization.

## Candidate preparation

From the exact clean release commit, build into an empty directory, create the
examples archive, and validate all artifacts:

```sh
uv lock --check
uv build --out-dir build/release/openrtl-0.2.0
python tools/validate_release.py \
  --dist-dir build/release/openrtl-0.2.0 \
  --commit FULL_RELEASE_COMMIT \
  --build-examples \
  --write
```

Clean-install verification must build or otherwise supply the immutable
AgentRig 0.2.2 wheel, install that dependency and the OpenRTL wheel into a new
environment, extract the examples archive, and run:

```sh
python tools/verify_release_install.py \
  --examples-root /path/to/openrtl-examples-0.2.0 \
  --with-verilator
```

The immutable candidate manifest records that tag creation was still pending
when its artifact bytes were qualified. The subsequently authorized annotated
tag `v0.2.0` and GitHub release both resolve to attestation commit
`fe5f0db1604b1bd33b2b94107d5c3d1f603a1a1a`; the qualified package commit is
`83fb441a29dd655397fc6cfd7615538c0aecde5a`.

## Public consumer acceptance

The repository includes a public-only acceptance lane:

```sh
python tools/validate_public_release.py \
  --output-directory build/public-release-acceptance \
  --with-verilator
```

It downloads the public manifest, wheel, source distribution, and examples
archive and verifies their exact qualified bytes. Because the OpenRTL wheel
pins `agentrig==0.2.2` and that Python package is not supplied by the OpenRTL
release, the lane checks out public AgentRig commit
`b03087d1040b40e1d7d1efc98439d501964567c6`, verifies the commit and installed
version, and installs it before OpenRTL. It never uses current AgentRig `main`.

The lane then safely extracts the public examples archive and runs the model,
fault-diagnosis, and Verilator repair walkthroughs in the isolated environment.
CI repeats this path from public inputs. Package-registry publication, signing,
provider calls, and deployment remain outside this acceptance contract.
