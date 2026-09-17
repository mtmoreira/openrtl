# Local web workspace (W3 simulation candidate)

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
language turns require an explicit launch selection, the optional pinned SDK,
and a supported credential environment name. An example command shape is:

```sh
./openrtl ui --project /absolute/existing-project --allow-provider --model EXACT_MODEL_ID --credential-env OPENAI_API_KEY
```

This command shape is documentation, not authorization to run a provider call.
The credential value never belongs in the browser, command arguments or project
files. Each launch selects authority anew. A provider-backed turn may transmit
the bounded context to the selected provider under its retention policy.

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
for a different design input. Compiler transport qualification and embedded
waveforms remain open acceptance work.
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
