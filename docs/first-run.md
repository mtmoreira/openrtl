# OpenRTL first-run candidate

This is M41 implementation work, not a qualified agent release. M46 live design
qualification and M47 release remain pending. Local review and deterministic
setup tests do not prove that a provider designed new RTL.

From a clone, run:

```sh
./openrtl
```

The launcher first checks for Python 3.12 or newer. Select a particular existing
interpreter with `OPENRTL_PYTHON=/absolute/path/to/python ./openrtl`; an unavailable
explicit interpreter stops setup without installing a replacement. If no usable
default Python exists, the candidate offers private uv 0.12.3 / CPython 3.13.15
(build 20260807) provisioning. Actual private provisioning and offline reuse
passed on the owner's macOS Apple Silicon host with no Python in the launcher's
PATH. This is isolated-state evidence, not a clean-OS-user qualification.
macOS Apple Silicon and Linux x86-64 with glibc are candidate
targets; minimum OS/glibc versions and clean-user tests remain pending. Other
runtime targets fail closed. Native Windows is not qualified.

When the pinned application dependency is missing, the launcher explains the
download and asks permission. Declining leaves setup stopped. The dependency is
AgentRig 0.3.0, read from the exact public wheel and hash in
`bootstrap/dependencies.json`; the launcher never resolves an editable sibling
checkout or silently accepts an unrelated installed AgentRig. The pure wheel is
loaded from a private cache without pip, build backends or installation hooks.
Every normal launch rehashes its bytes. The cloned OpenRTL source is the selected
application, so review changes to that checkout as you would any executable
source. This does not authenticate the checkout's origin.

Startup disables inherited Python search paths and site initialization. The
SDK-only evaluation environment and development package installations are not
silently used as product dependencies. Optional SDK setup uses the checked-in,
hashed 16-distribution OpenAI SDK 2.47.0 lock and requires a separate explicit
choice. Actual installation of those locked packages and offline SDK readiness
passed in private macOS state without provider calls. The existing installed
development CLI's separately authorized provider workflow remains documented in
the alpha guide; it does not qualify customer onboarding.

## Setup and review

The first-run flow saves a selected model identifier, the **name** of a credential
environment variable, and explicit call/repair/output/timeout bounds. Do not paste
a credential into setup or chat. Setup does not read or check the variable's
value. The default model is the unqualified trial
`gpt-5.4-nano-2026-03-17`; an existing choice is never upgraded automatically.
Saved preferences grant no provider, credential, download or simulation authority.

Choose a short project name to create a local session; choosing it again resumes
that session. The current local flow can load `/spec /absolute/path/spec.json`,
display `/show`, and preserve `/quit` state. Natural-language assistance and
new-design engineering still require the later explicitly authorized provider
and runtime flow. Requirement approval and final acceptance remain distinct.

Useful setup commands:

```sh
./openrtl doctor
./openrtl doctor --json --require-local
./openrtl setup --noninteractive --model gpt-5.4-nano-2026-03-17 --max-calls 20
./openrtl --state-dir "/absolute/private directory" setup --noninteractive
./openrtl setup-sdk
```

`doctor` is read-only and performs no daemon contact, model call or credential
resolution. Its default successful exit means diagnostics completed, not that a
design can be generated or simulated. `--require-local` requires exact local
dependency/interpreter readiness; SDK version, runtime configuration, isolated
self-test and live design qualification remain separate claims.

Product state defaults to `~/Library/Application Support/OpenRTL` on macOS and
`${XDG_STATE_HOME:-~/.local/state}/openrtl` on Linux. A relative XDG state path is
rejected. New state directories are private (0700), files are private (0600), and
links or unsuitable existing directories are rejected without changing their
permissions. Setup writes preferences atomically. It preserves completed cache
entries and prior preferences across interruption; it does not reset session
budgets or replay uncertain engineering operations.

## Unattended and offline use

Explicit `chat`, `resume`, `batch` and other existing CLI commands are forwarded
without setup prompts. If a required dependency is absent, unattended use stops
with exit 2 and instructions; it never hangs awaiting consent. Approve the
specific installation effect with `--allow-install` when preparing the cache:

```sh
./openrtl --allow-install chat --project /absolute/new-project
./openrtl --allow-install --offline --wheelhouse /absolute/wheels chat --project /absolute/new-project
```

The offline wheelhouse must contain exactly the selected AgentRig wheel filename
with the pinned hash and size. No network fallback is allowed under `--offline`.
Unrelated wheelhouse files are not read. A malformed, linked or changed selected
wheel fails closed. A missing public artifact or unavailable network stops with
an actionable diagnostic; no package index, alternate version or source build is
substituted. Download consent is separate from provider and simulation permission.

Download failures distinguish a missing public artifact, anonymous-access refusal,
rate limiting, other HTTP errors, TLS verification, DNS, connection/time limits,
rejected redirects and artifact verification. Each has a corresponding recovery
hint. Diagnostics contain fixed categories rather than remote response bodies or
exception text. Never disable TLS verification or supply credentials to recover
an anonymous public dependency download. A Python TLS failure can be specific to
the selected interpreter even when another HTTPS client succeeds.

When Python is absent, `--allow-runtime-install` approves the pinned uv/Python
preparation. `--runtime-artifacts /absolute/artifacts --offline` supplies existing
archives instead of downloading them. The directory must contain
`uv-<target>.tar.gz` and
`cpython-3.13.15+20260807-<target>-install_only_stripped.tar.gz`, where `<target>` is
`aarch64-apple-darwin` or `x86_64-unknown-linux-gnu`. Artifact identities and
provenance are in `bootstrap/runtime-provenance.json`. The bootstrap limits uv
and Python archives to 64 MiB and 128 MiB respectively, checks SHA-256 before
execution/extraction, and runs uv against a verified local mirror in offline mode.
No unpinned runtime or source build is substituted. Runtime mirror state paths
containing `%`, `#`, `?` or backslashes are rejected; spaces are supported.

Optional provider SDK preparation is explicit, including in a terminal:

```sh
./openrtl setup-sdk --allow-sdk-install
./openrtl setup-sdk --allow-sdk-install --offline --runtime-artifacts /absolute/artifacts --sdk-wheelhouse /absolute/sdk-wheels
```

SDK setup prepares pinned uv if necessary and copies only wheels allowed by
`bootstrap/sdk-requirements.lock` into private, interpreter-specific state. The
offline SDK path needs the uv archive plus compatible wheels for every locked
distribution. It does not require a Python archive when using existing Python.
Online SDK setup uses PyPI; source builds, dependency re-resolution, package
hooks, keyring providers and automatic Python downloads are disabled. The SDK
import check never creates a client or resolves a credential. Neither
`--allow-install` nor `--allow-runtime-install` grants SDK consent, and
`--allow-sdk-install` does not grant provider or simulation permission.

Successful SDK state has a receipt covering every installed file and exact
distribution version. Normal launches check that receipt before adding the
cache to Python's search path; startup hooks and bytecode files are rejected.
Changed caches stop without replacement. Interrupted attempts remain in private
state for inspection and retries use new attempts. A killed runtime setup can
leave an empty `runtime/uv-0.12.3-<target>/setup.lock`; after confirming no setup
is running, remove only that empty directory with `rmdir` before retrying.
The bootstrap never guesses a process owner or kills an existing runner.
Runtime completion receipts record setup, not a tamper-proof installed-tree
attestation: the account owning private state can modify its own runtime.

The existing batch spec/delegation contract remains in the alpha guide; ordinary
batch policy input is M43 work. No-argument setup needs a terminal; noninteractive
preferences use `setup --noninteractive`.

## Remaining acceptance work

- M41: the 50 focused tests, strict typing, full repository suite, actual offline
  launcher review and existing FIFO canary passed. Retained local evidence is
  recorded in [the base-slice manifest](../evidence/milestones/m41-first-run-base.json).
  The runtime/SDK additions passed 66 focused tests, strict typing across eight
  files, 323 repository tests, both model suites and six offline launcher checks;
  [their evidence manifest](../evidence/milestones/m41-runtime-sdk-local.json) records
  the qualified implementation. The separately approved macOS real-setup run
  passed missing-Python provisioning, offline runtime reuse, actual locked SDK
  installation and offline SDK readiness. The subsequent diagnostic retry passed
  69 focused tests, 326 repository tests, both model suites, strict typing and
  six offline launcher checks. Both existing and privately provisioned Python
  received HTTP 404/410 for the pinned public AgentRig URL.
  Those HTTP failures are historical. The approved AgentRig v0.3.0 publication
  subsequently passed anonymous byte verification for the wheel, sdist and release
  manifest. The actual cloned launcher downloaded the public wheel into fresh
  private state and completed specification review, offline doctor and saved-state
  checks using the product-provisioned Python. No provider or Docker was contacted.
  [Publication evidence](../evidence/milestones/m41-publication.json) binds those
  results to the unchanged implementation. The bootstrap pin's original provenance
  remains historical; the publication attestation records the later verification.
  M41a local implementation is validated. M41b Linux/clean-OS-user acceptance
  remains pending: private owner state is not a fresh OS account. This split
  permits independent local implementation without claiming the original M41
  acceptance gate or a supported-platform release is complete.
  [The setup and public-dependency evidence](../evidence/milestones/m41-public-dependency.json)
  binds the successful local checks and the failed public download separately.
- M42: select and qualify an owned isolated runtime/image with explicit consent.
  Preserve all existing unrelated Docker runners; there is no host fallback.
- M43–M45: complete conversational review, import/evolution, export and diagnostics.
- M46: freeze a candidate and qualify real provider-generated designs, with an
  independent adequacy review and retained meaningful waveforms.
- M47: clean-user tests, distinct release identity and separately authorized
  publication. Published toolkit v0.4.0 assets remain unchanged.

Temporary owner-shell development scripts are not part of this customer setup
interface and are not distributed as onboarding commands.
