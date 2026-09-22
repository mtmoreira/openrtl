# Local web workspace (W1–W5 implementation candidate)

This local candidate presents a project through a loopback browser interface.
It reuses the same DesignAgent operations and SQLite session as the CLI. W1
provider-free tests exercise the interface contract; they do not qualify a
provider, simulator or newly designed RTL. M46 and M47 remain pending.

Detailed telemetry requires the local AgentRig `0.3.1.dev13` candidate, which
adds its opt-in private trace API. This candidate has not been published. Its
bootstrap wheel hash, size and source provenance must come from the actual
reviewed owner build; the launcher stops while those pins are pending. Once the
reviewed wheel directory has been supplied, the normal launcher can prepare it
with `--allow-install --offline --wheelhouse <provided-directory>`. That is a
command shape: replace `<provided-directory>` with the supplied directory, and
do not substitute a dev12 wheel or assume the candidate release URL is live.
The offline setup does not install provider SDKs, start Ollama, or make a call.

From a checked-out clone with the pinned application dependency prepared, serve
a fixed project path:

```sh
mkdir -p "$HOME/OpenRTL-projects"
./openrtl ui --project "$HOME/OpenRTL-projects/circuit"
```

The launcher prints `http://127.0.0.1:8765`. Open that address in a desktop
browser and select **Create this project**. The server can create only the path
selected at launch; its parent must already exist. Reopen it with the same
command:

```sh
./openrtl ui --project "$HOME/OpenRTL-projects/circuit"
```

`--create` is an optional launcher shortcut that creates the selected project
before the page loads.
For an older session, the page shows an explicit **Upgrade this project
session** action. It appends a schema migration event before enabling edits;
opening the project alone does not migrate it.

The default interface permits local inspection without a provider. Natural
language turns require an explicit launch or web selection and the matching
optional pinned SDK. An OpenAI command shape is:

```sh
./openrtl ui --project /absolute/existing-project --allow-provider --provider openai \
  --model EXACT_MODEL_ID --max-spend-usd 20.00 --credential-env OPENAI_API_KEY
```

An Ollama selection uses the fixed loopback service, an already-installed
model, no API key and no OpenRTL USD ceiling:

```sh
./openrtl setup-sdk --allow-sdk-install
./openrtl ui --project /absolute/existing-project --allow-provider \
  --provider ollama --model qwen3:8b
```

These command shapes are documentation, not authorization to run a provider call.
Never put a credential in chat, command arguments or project files. The dedicated
password field in Provider settings can replace the running server's key;
the server does not return or persist it. Each launch selects authority anew.
An OpenAI-backed turn may transmit
bounded context under OpenAI's retention policy. Ollama remains at
`http://127.0.0.1:11434`; OpenRTL neither starts that service nor pulls models.

The browser shows a conversation plus saved engineering facts,
specification revisions, review cards and bounded operation events. It saves
structured proposed requirements, assumptions, decisions and questions with
stable IDs and attribution. Prompt and reply text remain ephemeral by default.
A refresh or restart reconstructs engineering state without replaying a turn.

Use `--timeout-seconds 240` on `ui` or change **Request deadline (seconds)** in
Provider settings to allow a longer request. The accepted range is 1–300 seconds;
the default is 120. Earlier attempts ending at roughly 120 seconds could appear
as a generic provider failure: typed deadline/cancellation failures had no
provider error code, and a second equal application timeout could race the
adapter's timeout. The adapter now owns the deadline and OpenRTL reports typed
timeout/cancellation outcomes. A timed-out remote attempt can still have an
unknown external outcome; changing the deadline does not reconcile or replay it.

For explicit local capture, launch `ui` with `--capture-details` or select
**Capture detailed operation records locally** and save Provider settings.
Capture stores submitted text, model requests and outputs, provider-returned
reasoning when supplied, and available artifact, process, tool, runtime and
usage records in private local session tables. This may include sensitive
design content. Credentials are excluded; ordinary events remain bounded and
content-free. Capture consent is not restored by a saved project: a restart
defaults to off unless the flag is explicitly supplied again. Turning capture
off stops new capture without erasing prior records. Captured conversation can
be shown again on reopen and is labeled **Saved local capture**. Older attempts
without capture cannot have their missing prompts, replies or reasoning
reconstructed afterward. Model internals the provider does not expose remain
unavailable. Private trace tables are excluded from ordinary session exports.
The Design Lead reviews existing engineering memory before asking anything new.
It batches at most three high-information questions in a round, aims to produce
a reviewable specification after one round and normally stops clarifying by the
third. A later round is valid only for a new concrete blocker involving the
interface, clock/reset or CDC safety, externally visible behavior, or acceptance.
The application rejects an unchanged repeated question set and any round with
more than three materially new or refined questions. Routine choices are stated
as reviewable assumptions, and a permission-only question does not delay a ready
specification.

If a completed model response fails deterministic discovery validation, the
System message in Conversation identifies whether the proposed readiness,
questions, specification, memory or structured fields failed. The matching
failed operation in History includes an allowlisted `validation_code` for the
exact rule. The rejected proposal does not replace the saved specification or
erase earlier answers, and the request is never replayed automatically. These
codes describe local checks, not raw model output; detailed capture remains
subject to its separate opt-in.

Operation notices, progress and errors appear in Conversation with a **System**
identity. Updates reconcile the same operation, and reconnect never resubmits
it. History and Activity rows inspect details without switching the workbench's
source revision; use the explicit source-revision buttons when needed.
Expandable capture records display untrusted content as text. Saved detail shows the
selected event, related operation timeline, provider and model identity, prompt
and context schema versions, token use, elapsed time, bounded cost fields,
failure codes and matching simulation diagnostics when those fields were
persisted. Missing usage, cost, reasoning or tool/process records are labeled
unavailable, rather than zero or a fabricated transcript. Design Lead turns
have no tool or shell authority; real process activity is shown only when
recorded by the simulation/provider adapter. Simulation evidence retains its
exact run and source identity. Captured reasoning is provider-returned text,
not access to hidden internal computation or proof of engineering correctness.

The browser sends a unique operation ID and expected revision. Retrying that ID
with the same request returns the saved state; a changed request or stale tab is
rejected. Closing a browser tab does not cancel work. A queued operation can be
cancelled before provider dispatch. Once active, cancellation is reported as
uncertain until the underlying operation is reconciled; stopping the local task
does not establish that the remote provider call was cancelled.

The server binds only `127.0.0.1`, serves packaged assets, checks Host and
Origin, and fixes the project at launch. It exposes no arbitrary file path or
credential route. Only one session writer can hold a project. The browser does
not inherit runtime or provider authority from saved project state.

The workbench reads saved source at an exact revision, path and content digest.
Its history and source diff inspect prior snapshots without changing the current
project. The planned top and DV test modules come from the specification and
manifest; requirement-to-test links describe the plan and make no coverage
claim. Change review shows the complete saved proposal and uses the same
revision- and digest-bound approval as the CLI. Approved changes invalidate
the current manifest and simulation evidence through the existing design state
machine. Post-discovery questions can attach a checked source revision and
bounded line range. Raw question and context text are retained only with
explicit detailed capture.

An actual elaborated instance hierarchy requires a compiler-produced index.
The Verilator 5.046 JSON adapter can ingest bounded `.tree.json` and
`.tree.meta.json` bytes from a trusted compiler transport. It follows CELL
references to MODULE definitions, preserving repeated instance paths,
generated names and specialized module identities. The workbench checks every
source link against the exact saved revision and digest before displaying it.
Synthetic fixtures exercise the parser contract; they are not evidence that
the owned compiler transport is qualified. Until that transport supplies a
validated index, the interface states that elaboration is unavailable while
keeping source inspection usable. The index is process-scoped and must be
recomputed after service restart or source change; it is never silently reused
for a different design input. Live compiler transport qualification remains
open acceptance work.
Historical passing results never qualify a changed source digest.

The simulation panel displays a digest-bound configuration for the current
complete baseline, including top, sources, test selection, seed, timeout and
bounded runtime identity. An explicit Run action submits one durable operation
ID. Reconnect reads the saved result; a stale plan or competing writer is
rejected. The panel labels evidence whose input digest no longer matches the
current design as historical. Cancellation of active runtime work remains
uncertain until the owned container is reconciled.

The browser service exposes simulation only when the invocation selects both a
self-test-verified runtime state and an owned Lima guest transport:

```sh
./openrtl ui --project /absolute/existing-project --allow-simulation \
  --runtime-state /absolute/private/runtime-state \
  --lima-executable /absolute/path/to/limactl \
  --lima-state-root /absolute/private/managed-state \
  --lima-instance managed-EXACT_INSTANCE_ID
```

This selection authorizes runtime contact only when the designer explicitly
submits the displayed run. The transport copies task-owned input and control
files to the selected running guest without changing host staging permissions
or starting the VM. Before the design container starts, a separate container
under the selected daemon and UID 65534 hashes the exact read-only mounts and
compares every staged file. A mismatch fails closed. The durable runtime intent
also binds the transport identity and a probe container name so an uncertain
probe or run can be reconciled by exact identity. The browser can request
recovery after reopening the project; interrupted output is never adopted.

This code and its synthetic tests do not establish that a particular owned
Lima VM, selected Docker daemon, and packaged Verilator/cocotb image work
together. That live workload visibility and fresh-design qualification remain
pending, as do M46 and M47.

The recorded waveform panel lists passing and failed simulation reports from
saved project history. It opens a trace only when the run ID, retained artifact
path, byte count and SHA-256 still match; it never accepts a browser-provided
filesystem path. Missing, changed, oversized and unsupported traces remain
unavailable with an explicit reason. Signal search returns at most 128 names;
window queries return at most eight signals and a bounded number of transitions
per signal. A truncated row has no inferred values beyond its last returned
transition. Zoom or pan to inspect a smaller interval. The browser renders
scalar levels and vector change markers, preserves X/Z values, offers binary,
hexadecimal and unsigned-decimal cursor values, and shows two cursor times and
their difference in femtoseconds. A failed run opens its full trace without
claiming an exact failure-time anchor that the runner did not record.

**Save selection in this browser** explicitly stores only the run ID, trace
digest, selected signal names and time interval in that browser's local
storage. Reload restores it only if the retained trace digest still matches.
**Attach run and interval to conversation** sends that bounded, exact run
selection with the next question. OpenRTL rechecks the run and trace before
constructing the expert's context; raw question and context text are retained
only with explicit detailed capture. The current viewer uses a bounded VCD adapter. It does not
claim FST support, a live viewer performance target, or a precise failure
source/time link without corresponding evidence.

The checked-in `./openrtl` launcher serves the three web assets directly from
the clone. It needs no frontend build step. The provider-free onboarding smoke
copies the launcher, application modules and exact web assets into a temporary
clone, uses a pinned retained AgentRig wheel in private test state, and starts
the loopback UI twice: first to create a project, then to reopen it. It checks
the served asset bytes, stable project identity, saved revision and empty run
history. This proves local packaged asset and reopen behavior in that test
environment; it does not run a provider, compiler, simulator or browser GUI.

For an engineering-state transfer, use the existing reviewed `sessions export`
and `sessions restore` commands described in [session diagnostics](sessions-diagnostics.md).
The digest-bound portable format retains snapshots, events, source blobs and
the evidence files of each saved runtime-bound simulation. It transfers no
provider or runtime authority. Export requires a reconciled session and an
explicit review digest. The browser's explicit waveform selection is stored
only in that browser's local storage and is not part of a project export.

The local tests and launcher smoke do not establish a fresh provider-designed
circuit, a live owned-guest simulation, real-trace viewer performance, or an
end-to-end repair and rerun. Those require separate owner-authorized live
qualification with actual tool and artifact evidence. M46 and M47 remain
pending; this workspace candidate is not a new published release.
