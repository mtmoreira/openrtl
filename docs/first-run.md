# OpenRTL first-run candidate

This is M41 implementation work, not a qualified agent release. M46 live design
qualification and M47 release remain pending. Local review and deterministic
setup tests do not prove that a provider designed new RTL.

From a clone, run:

```sh
./openrtl
```

The current candidate requires an existing Python 3.12 or newer. It checks that
prerequisite before doing anything else. Select a particular existing interpreter
with `OPENRTL_PYTHON=/absolute/path/to/python ./openrtl`. Managed Python
provisioning is still pending; a fresh machine without Python is not yet a
passing M41 installation. macOS Apple Silicon and Linux x86-64 are proposed
targets, contingent on M47 clean-user tests. Native Windows is not qualified.

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
silently used as product dependencies. Guided SDK provisioning remains pending;
the launcher therefore does not yet enable live model use. The existing installed
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

The existing batch spec/delegation contract remains in the alpha guide; ordinary
batch policy input is M43 work. No-argument setup needs a terminal; noninteractive
preferences use `setup --noninteractive`.

## Remaining acceptance work

- M41: execute the new tests and actual launcher review path; qualify public
  download availability, managed Python provisioning and SDK setup. No new
  installer production dependency is silently introduced by this candidate.
- M42: select and qualify an owned isolated runtime/image with explicit consent.
  Preserve all existing unrelated Docker runners; there is no host fallback.
- M43–M45: complete conversational review, import/evolution, export and diagnostics.
- M46: freeze a candidate and qualify real provider-generated designs, with an
  independent adequacy review and retained meaningful waveforms.
- M47: clean-user tests, distinct release identity and separately authorized
  publication. Published toolkit v0.4.0 assets remain unchanged.

Temporary owner-shell development scripts are not part of this customer setup
interface and are not distributed as onboarding commands.
