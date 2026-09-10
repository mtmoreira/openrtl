# Local backend review

OpenRTL owns simulation requirements and engineering evidence. AgentRig owns
generic local backend contracts and backend-specific configuration. Optional
`runtime backends` and `runtime backend-plan` commands review registered backends
without reading or changing runtime state. Backend substitution does not change
RTL or design-session code.

These commands require the separately identified AgentRig `0.3.1.dev2` local
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

Existing explicit current-user rootless Docker selection and fixed self-test
remain the execution path. Backend review cannot overwrite that selection or
bypass active-operation and stale-evidence checks. A future managed backend must
provide a verified transport and pass those checks before selection. Historical
runs must retain their original runtime identity.

M42b runtime qualification, M46 real-agent new-RTL qualification and M47 release
remain pending. Offline tests, the fixed self-test and existing FIFO canary are
not evidence that an agent designed new RTL.
