# OpenRTL release contract

OpenRTL 0.2.0 is the first release candidate that packages the complete V1
simulation and evidence-driven repair workflow. The immutable release identity
is one semantic version, one exact commit, and these artifacts:

- `openrtl-0.2.0-py3-none-any.whl` — the typed OpenRTL library and CLI;
- `openrtl-0.2.0.tar.gz` — source distribution including examples;
- `openrtl-examples-0.2.0.tar.gz` — normalized runnable examples; and
- `openrtl-0.2.0-release.json` — hashes and sizes for the preceding artifacts.

The wheel retains the exact base dependency `agentrig==0.2.2`; provider SDKs
remain opt-in and are not needed by the provider-free verification lane.

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

The release manifest plans tag `v0.2.0` but does not create it. An annotated
tag, GitHub release, registry upload, signing policy, and any remote delivery
require separate explicit authorization after the local candidate is verified.
