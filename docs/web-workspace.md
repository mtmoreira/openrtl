# Local web workspace (W1–W5 implementation candidate)

This local candidate presents a project through a loopback browser interface.
It reuses the same DesignAgent operations and SQLite session as the CLI. W1
provider-free tests exercise the interface contract; they do not qualify a
provider, simulator or newly designed RTL. M46 and M47 remain pending.

From a checked-out clone with the pinned application dependency prepared, serve
a fixed project path:

```sh
./openrtl ui --project /absolute/new-project
```

The launcher prints `http://127.0.0.1:8765`. Open that address in a desktop
browser and select **Create this project**. The server can create only the path
selected at launch; its parent must already exist. Reopen it with the same
command:

```sh
./openrtl ui --project /absolute/new-project
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
The credential value never belongs in the browser, command arguments or project
files. Each launch selects authority anew. An OpenAI-backed turn may transmit
bounded context under OpenAI's retention policy. Ollama remains at
`http://127.0.0.1:11434`; OpenRTL neither starts that service nor pulls models.

The browser shows an ephemeral conversation plus saved engineering facts,
specification revisions, review cards and bounded operation events. It saves
structured proposed requirements, assumptions, decisions and questions with
stable IDs and attribution. It does not persist raw prompt or reply text. A
refresh or restart reconstructs engineering state rather than replaying a turn.
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
bounded line range without persisting the question or source excerpt.

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
constructing the expert's context; it does not persist the raw question or
waveform excerpt. The current viewer uses a bounded VCD adapter. It does not
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
