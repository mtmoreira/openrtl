# Local backend review

OpenRTL owns simulation requirements and engineering evidence. AgentRig owns
generic local backend contracts and backend-specific configuration. Optional
`runtime backends` and `runtime backend-plan` commands review registered backends
without reading or changing runtime state. Backend substitution does not change
RTL or design-session code.

These commands require the separately identified AgentRig `0.3.1.dev3` local
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

The default Lima backend is **planning only**. Guest artifacts, lifecycle activation,
private staging and forwarding, resource isolation and real recovery qualification
remain pending. It advertises no
execution capability. Hashes or synthetic receipts cannot remove these blockers.

The SDK also contains a separate, unregistered lifecycle driver candidate with
bounded command execution and durable operation recovery. Its actual artifact
verifier, approved guest bundle, staging and transport qualification remain
pending; the default review backend cannot activate that driver.

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

M42b runtime qualification, M46 real-agent new-RTL qualification and M47 release
remain pending. Offline tests, the fixed self-test and existing FIFO canary are
not evidence that an agent designed new RTL.

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
