# Managed simulation candidate

The optional [local backend review](local-backends.md) adds a replaceable AgentRig
setup boundary. Lima is planning-only; real VM setup and qualification remain
pending. The explicit rootless endpoint path below is unchanged.

M42 is under implementation. The current local slice adds selection and a fixed
self-test for an explicitly reviewed **current-user rootless Docker** endpoint.
It does not install or qualify a macOS VM, publish simulator images, or qualify
agent-generated RTL. Complete managed onboarding and the real isolated runtime
acceptance gate are pending. M46 live design qualification and M47 release are
also pending.

The local M42a slice passed owner-environment validation: 352 repository tests,
both three-test model suites, 92 focused regressions, strict typing across ten
files and offline actual-launcher checks. [Its evidence manifest](../evidence/milestones/m42-local-runtime.json)
binds the reviewed source and retained logs. Runtime process tests and seeded
selection state are synthetic; no Docker runtime was contacted. M42b keeps the
managed backend, image distribution and real acceptance gate open while later
independent local milestones proceed.

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

Until the managed backend and image distribution are qualified, the following
is an advanced evaluation interface, not the promised complete customer setup.
Use only an already approved owned rootless endpoint and reviewed existing image:

```sh
./openrtl runtime select --docker /absolute/docker --socket /absolute/private/docker.sock \
  --image sha256:<reviewed-local-image-id> --architecture arm64 \
  --verilator <reviewed-major.minor> --allow-runtime-contact
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
of hand-written `--simulation-profile` JSON. Version-2 profiles require that
state route and a current self-test; legacy version-1 explicit profiles remain
an advanced alpha interface and do not gain new readiness claims.

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

The required real self-test has not been run by this local implementation. Unit
tests use clearly labeled process doubles and cannot close that acceptance gate.
