# Managed simulation candidate

The optional [local backend review](local-backends.md) adds a replaceable AgentRig
setup boundary. The current Lima implementation has qualified an owned Linux
arm64 VM, authenticated forwarding and a stopped-by-default rootless Docker
daemon. Those observations qualify that backend instance only. The explicit
rootless endpoint path below is unchanged.

M42a adds selection and a fixed self-test for an explicitly reviewed
**current-user rootless Docker** endpoint. M42b has now qualified one owned Lima
2.2.0/rootless Docker instance and the exact local arm64 simulator image
`sha256:500f6522989b4ec75b5122d66c1adc74a3b04183548936cface7641fab33e6cf`.
Verilator 5.046 and cocotb 2.0.1 passed the deterministic isolated fixture under
the reviewed CPU, memory, PID, capability, mount and network limits. This does
not publish the image or qualify agent-generated RTL. M46 live design
qualification and M47 release remain pending.

The local M42a slice passed owner-environment validation: 352 repository tests,
both three-test model suites, 92 focused regressions, strict typing across ten
files and offline actual-launcher checks. [Its evidence manifest](../evidence/milestones/m42-local-runtime.json)
binds the reviewed source and retained logs. Runtime process tests and seeded
selection state are synthetic; no Docker runtime was contacted in that earlier
slice. The tracked M42b live attestation now binds the retained valid receipt and
keeps the earlier false-positive JUnit receipt visibly invalid. Checked-in
managed lifecycle inspection has passed. The backend-neutral guest-retirement
consumer composes AgentRig's durable retirement journal and Lima generation
binding. The concrete authenticated fence/removal/observation ports and the
already-absent-forward inspect recovery have passed against the owned instance.

The read-only starting point is:

```sh
./openrtl runtime plan
./openrtl runtime status
```

These commands do not contact a daemon, enumerate other accounts' containers,
read Docker authentication configuration or restore execution permission.
`--json` provides bounded machine-readable status. A saved passing self-test is
rehash-checked locally; daemon and image identity are checked again at execution.
Default `doctor` still describes application prerequisites; use `runtime status`
for this separate selection and evidence state.

## Existing-runtime evaluation

The following explicit endpoint interface remains available for advanced
evaluation. Use only an already approved owned rootless endpoint and reviewed
existing image:

```sh
./openrtl runtime select --docker /absolute/docker --socket /absolute/private/docker.sock \
  --image sha256:<reviewed-local-image-id> --architecture arm64 \
  --python /exact/in-container/python --verilator <reviewed-major.minor> \
  --allow-runtime-contact
./openrtl runtime self-test --allow-runtime-contact
```

The placeholders are not executable pins or published images. No daemon is
started and no image is pulled. Selection refuses links, other-account sockets,
paths writable by other users, non-rootless daemons and mismatched image/daemon
architectures. It binds the executable bytes, socket device/inode/UID, daemon ID,
local image content ID, architecture, tool version and bounded resources into
an internal profile; users do not write profile JSON. Selection alone is not a
passing self-test, image-origin attestation, or design-execution permission.

Defaults are 2 CPUs, 2048 MiB memory, 128 processes and 256 MiB output. Selection
accepts bounded `--cpus`, `--memory-mib`, `--pids`, `--output-mib` and
`--timeout-seconds` values. Containers retain no network, no host home/credentials
or daemon socket mounts, a read-only root and inputs, nonroot UID, dropped
capabilities and no-new-privileges. There is no host execution fallback.

The fixed XOR self-test is infrastructure collateral, never a production design
generator or evidence of agent intelligence. It checks a small truth table,
model tests, tool versions, nonroot/capability/mount/network-interface properties,
cgroup-v2 resource enforcement and a changing output waveform. Missing cgroup
controllers or evidence fails closed. The runner, inputs, request, intent and
all five simulation artifacts remain available under private runtime-check state.
Hashes establish local consistency, not independent authentication of execution.

After an actual passing self-test, an explicitly authorized design invocation can
use `--allow-simulation --runtime-state /absolute/private/OpenRTL-state` instead
of hand-written `--simulation-profile` JSON. A runtime reached through the owned
Lima guest must run `runtime self-test` with the exact `--lima-executable`,
`--lima-state-root` and `--lima-instance` selection later supplied to the design
or web invocation. The self-test receipt binds that transport identity, and
readiness fails closed if it is omitted or changed. Explicit recovery of an
interrupted transport-bound self-test requires the same three fields and never
replays the test. `runtime status` rehashes the saved evidence without restoring
transport or execution authority. Version-2 profiles require that state route
and a current self-test; legacy version-1 explicit profiles remain an advanced
alpha interface and do not gain new readiness claims.

## Simulator image source

[`simulation/image-source.json`](../simulation/image-source.json) is the exact,
offline acquisition input for the first Linux arm64 simulator image. It pins the
Verilator 5.046 base by platform manifest digest and pins cocotb 2.0.1 plus its
pure-Python `find-libpython` dependency to the URLs, hashes and sizes already in
`uv.lock`. The contract contains no backend executable, VM, socket, credential or
lifecycle authority. Lima and any future local backend consume the same resulting
local image content ID.

Validate the source and lock binding without network or runtime contact:

```sh
python tools/validate_simulator_image_source.py
```

The contract permits acquisition only. It forbids container execution, image
construction and package installation before the acquired bytes and image
metadata are retained and reviewed. The exact source was acquired and audited,
then used to construct the retained local image named above. That image passed
the fixed self-test. The source digest still cannot substitute for the observed
local content ID or passing runtime receipt, and no registry image is published.

## Interruption and recovery

Runtime selection, self-test and recovery use a private nonblocking writer lock.
A second writer stops; a process exiting releases its lock. Atomic files retain
the previous complete value if interrupted. State never stores authority.

An interrupted self-test is not automatically rerun. Inspect `runtime status`,
then authorize `runtime recover --allow-runtime-contact` for its recorded
operation. Recovery verifies the selected endpoint, exact container name, image
and operation label before removing only that container. Missing ownership
evidence requires manual reconciliation; no resource is guessed. Previous run
files and failures are retained, and recovered state still needs a new explicit
self-test. Selection cannot overwrite an unresolved operation.

## Remaining managed-backend gate

Colima remains a candidate dependency; it has not been adopted or installed.
An approved macOS backend must use an owned private VM/profile and explicit
image provenance without changing global Docker contexts, sockets, SSH settings
or the `agents` account's always-on runner. Backend dependencies, private runtime
installation/downloads, runtime control and image acquisition remain separate
approval boundaries. Native Windows and broader host/platform claims are pending.
The checked-in managed control and retirement commands remain modular and require
exact per-invocation effects. The qualified Lima CLI ports are the default for
the `lima-vz-managed` retirement backend; selecting them performs no runtime
action, and callers can replace the complete registration mapping. Customer
clone/run acceptance remains separate from this development integration.

The default Lima CLI port implementation shares an exclusive application fence with
managed lifecycle calls, re-audits the immutable local closure around every
bounded observation and retains the transport workspace and service lock. It
does not equate Lima's automatic forward removal during VM stop with an OpenRTL
unlink: that state is recovered by a fresh inspect-only operation. Default
selection does not alter clone/run setup or prevent injection of another
registration mapping.
