# Local backend review

OpenRTL owns simulation requirements and engineering evidence. AgentRig owns
generic local backend contracts and backend-specific configuration. Optional
`runtime backends` and `runtime backend-plan` commands review registered backends
without reading or changing runtime state. Backend substitution does not change
RTL or design-session code.

These commands require the separately identified AgentRig `0.3.1.dev12` local
candidate. The public clone-and-run launcher still uses immutable published
`0.3.0`; existing commands continue to work. With that SDK, the new commands report
that the candidate is required. Temporary development handoffs are not customer
setup prerequisites. Nothing is installed or downloaded by backend review.

In a development environment containing the reviewed source candidate:

```sh
python -m openrtl.cli runtime backends --json
python -m openrtl.cli runtime backend-plan --backend lima-vz --action prepare \
  --operation-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --config-json '{"host":"darwin","architecture":"arm64","state_root":"/private/tmp/review-only","cpus":2,"memory_mib":2048,"disk_gib":20}' \
  --json
```

The output is a review with a digest and blockers. It does not create the example
directory, select a runtime, restore authority or contact a daemon. Changed inputs
or operation invalidate the digest. Unknown backends and duplicate/oversized
configuration fail closed. Configurations are explicit review data, not logs;
never supply secrets in them.

The default `lima-vz` backend remains **planning only**. A distinct
`lima-vz-managed` lifecycle adapter is available through the managed commands
below. Keeping the review descriptor separate prevents a saved review from
silently becoming execution authority.

The SDK lifecycle driver has bounded command execution and durable operation
recovery. OpenRTL composes it through a replaceable application registry and an
adapter-specific artifact verifier. Another local backend can implement the same
plan/apply/reconcile seam without changing RTL, design sessions, runtime profiles
or simulator evidence.

## Managed lifecycle control

`runtime managed-config` audits an existing private Lima state root and emits
the exact configuration consumed by the managed lifecycle planner. It hashes the
owned `limactl`, restricted `configuration.json` and `artifacts/guest.raw`; it
does not start Lima, install anything, read instance key material or contact a
provider. The stable closure permits only Lima's private generated `_config`
entries (`networks.yaml`, `user` and `user.pub`) and rejects every other global
entry. The configuration is review data and may contain private local paths, so
store it only in owner-private state when it must be retained.

```sh
./openrtl runtime managed-config --backend lima-vz-managed \
  --executable /absolute/path/to/limactl --state-root /absolute/private/state \
  --instance-id 0123456789abcdef0123456789abcdef --json
```

`runtime managed-plan` is pure: it accepts the emitted `configuration` object as
`--config-json`, an action and a fresh 32-character lowercase hexadecimal
operation ID. It returns the exact plan digest and required effects without
reading or changing runtime state.

`runtime managed-apply` recomputes that plan and requires the reviewed digest
plus exactly its effect flags. Inspect and stop require
`--allow-local-write --allow-runtime-contact`; prepare also requires
`--allow-private-key-creation`; start requires all three plus
`--allow-runtime-start`. Extra grants fail just like missing grants. The command
records uncertainty before invoking Lima and never retries an ambiguous action.
Use the same operation and configuration with action `inspect`, a freshly
reviewed inspect digest and `--reconcile` to settle only an uncertain record by
observation. Saved journals and plans never restore authority.

Lifecycle success reports the owned VM state only. It does not select the Docker
socket, validate guest service transport, mark the simulator ready or authorize
a design run. Those checks stay in the existing runtime selection and fixed
self-test path.

## Guest-service retirement composition

`runtime retirement-plan` reviews retirement independently from VM lifecycle.
Its bounded configuration contains a freshly observed `endpoint` identity
(`root`, instance ID, device, inode and owner UID) plus backend-specific
generation data. For `lima-vz-managed`, that data binds the exact managed
configuration, a reviewed lifecycle inspection operation and a fresh generation
digest. The plan is pure and binds all of those values, the retirement operation
ID and the action in one digest.

```sh
./openrtl runtime retirement-plan --backend lima-vz-managed \
  --action retire --operation-id 0123456789abcdef0123456789abcdef \
  --config-json "$REVIEWED_RETIREMENT_CONFIGURATION" --json
```

The backend-neutral consumer accepts injected backend registrations. Retirement
requires exactly `local_write`, `runtime_contact` and `guest_socket_remove`.
Observation-only recovery uses action `inspect` and requires only `local_write`
and `runtime_contact`; that approval can never remove a socket. The coordinator
persists uncertainty before effects and rejects replay. A lost reply must be
settled with the same operation and endpoint through a newly reviewed inspect
plan and fresh backend observation.

The default Lima registration is intentionally review-only until the application
injects all three authenticated live ports: a generation fence that proves the
VM and forwarding stopped and clients drained, an exact socket retirer, and an
observer that proves the same generation is retired while its lock and workspace
remain. `lima_retirement_registration` provides this seam; partial registration
fails closed. Alternate local backends can provide the same pure validator and
adapter factory without changing the retirement consumer.

`lima_cli_retirement_registration` is the explicit Lima 2.2.0 candidate for
those ports; it is not selected by the default registry. It re-audits the exact
executable, configuration and guest-image closure around bounded,
environment-allowlisted `limactl list` observations. Managed lifecycle calls and
retirement share an application-owned exclusive fence. The retirement context
also requires the exact prior forward identity, a stopped `vz`/`aarch64`
instance, a private retained `.service.lock`, and an unchanged private transport
directory. The guarded AgentRig retirement port performs the only accepted
unlink.

Lima normally removes its host `guestSocket` forward while stopping the VM. A
missing socket therefore cannot be relabeled as an OpenRTL removal. A retire
attempt against that already-absent endpoint remains uncertain; a separately
approved `inspect` action may reconcile it only after fresh stopped-instance,
socket-absence, service-lock and workspace observations. The result records
reconciliation and no unlink effect. This path still needs live qualification.

`runtime retirement-status --state-dir PATH --json` reads only the local
retirement journal. Completed and reconciled records are historical evidence,
not authority. They do not mark a runtime ready, start or stop a backend, select
a simulator, or authorize a design run.

`python -m openrtl.cli runtime backend-operation --state-dir PATH --json` reads
the local backend-operation journal without creating a directory, taking a writer
lock, contacting a runtime or restoring authority. An `uncertain` record requires
fresh authorized instance inspection. This command is diagnostic; it does not
perform that inspection or change simulation selection/readiness.

`python -m openrtl.cli runtime backend-artifacts --artifact-root PATH
--manifest-json JSON --json` audits an explicitly supplied private directory of
reviewed public artifacts. The JSON schema is `agentrig.backend-artifacts.v1`
with an `artifacts` list; each entry has `id`, `path`, `sha256` (including the
`sha256:` prefix), `size_bytes`, and `executable`. The root must contain exactly
the declared files and their parent directories. Data files use mode 0600;
executables use 0700. No automatic permission repair occurs.

The command reads files for at most a 60-second context deadline and does not
create runtime state, contact a daemon, download or install. A matching manifest
is a content audit only; it never clears guest-dependency or runtime readiness
gates. Archive inventory, semantic Lima configuration and full guest closure
remain separate prerequisites.

Guest transport preparation must also preserve the existing container's UID
65534 and read-only `/input` and `/control` mounts. The SDK's private host staging
files are not automatically readable by that container user. A qualified guest
copy/access mechanism and actual visibility tests are required; sharing the host
home, weakening host staging permissions or using root is not an implementation
of this boundary. The default simulator still uses its existing explicit endpoint.

Existing explicit current-user rootless Docker selection and fixed self-test
remain the execution path. Backend review cannot overwrite that selection or
bypass active-operation and stale-evidence checks. A future managed backend must
provide a verified transport and pass those checks before selection. Historical
runs must retain their original runtime identity.

The owned macOS Lima/rootless Docker instance and exact arm64 simulator image
have passed the fixed isolated Verilator/cocotb self-test. The tracked
`m42b-runtime-live-qualification.json` attestation records that scope and the
invalid earlier receipt. Managed lifecycle inspection through the checked-in
command has now passed. The modular guest-retirement consumer is source-validated.
Its explicit Lima CLI ports remain a source candidate until live retirement and
recovery evidence is accepted. M46 real-agent new-RTL qualification and M47
release remain pending. The fixed self-test and existing FIFO canary are not
evidence that an agent designed new RTL.

## Inspect a restricted backend configuration

The optional candidate command `runtime backend-config --backend lima-vz
--policy-json JSON --json` audits an existing private `configuration.json` and
`artifacts/guest.raw`. The policy object requires `state_root`,
`guest_image_sha256` (`sha256:` plus 64 lowercase hex digits) and
`guest_image_size`. Optional resource fields are `cpus`, `memory_mib`, `disk_gib`
and `guest_uid`; defaults are 4, 4096, 20 and 1000. The input configuration must
exactly match AgentRig's `LimaConfiguration` renderer for that policy.

The SDK checks the local image bytes, rejects global Lima overrides and permits
only its restricted profile: no host mounts, arbitrary scripts, inherited image
templates or implicit Lima package installation. It allows only the two owned
Unix-socket forwards. Full guest dependency and license inventory, effective Lima
settings, service authentication and actual workload isolation remain unverified.
The audit does not create configuration files or change runtime selection.

Audit dispatch belongs to `adapters/backend_setup.py`; another backend can supply
its own auditor without changing the RTL engineering workflow. Reports keep
runtime, dependency and M46/M47 qualification pending. This candidate diagnostic
is development functionality, not a customer setup requirement; the clone-and-run
launcher still uses the immutable published SDK until separate release approval.
