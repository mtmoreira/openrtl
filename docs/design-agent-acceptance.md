# Evaluating the OpenRTL design-agent candidate

M36–M39 have local validated checkpoints. M40 is an implementation candidate;
its local checks and real generated-design acceptance are not yet recorded.
The published v0.4.0 toolkit is not this agent. Do not replace its release assets
with these development artifacts or identify this candidate by version alone.

## What each kind of evidence establishes

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| Scripted unit tests | Contracts, review gates, recovery and scope handling | Real provider generation or execution |
| Installed-target CLI smoke | Exact wheel package imports and local CLI workflows | Clean-machine dependencies, provider access or RTL correctness |
| Production FIFO canary | Existing example passes the observed simulator/toolchain | A newly generated design works |
| Reverified local acceptance report | Saved sources, receipts and gates are internally consistent | Authentic provider origin, independent attestation or exhaustive coverage |
| Observed live evaluation plus adequacy review | The particular frozen candidate and circuit passed the recorded checks | All possible designs, synthesis, formal proof, PPA or FPGA readiness |

## Read-only acceptance inventory

```sh
openrtl acceptance --project /absolute/path/to/project
openrtl acceptance --project /absolute/path/to/project --expected-spec /absolute/path/to/reviewed-spec.json
```

Exit 0 means `local_gates_satisfied`; exit 2 means `pending`; exit 1 means an
inspection/configuration error. The optional expected specification must match
the session exactly. The command does not approve anything, migrate the session,
call providers or run simulations. It emits JSON to stdout without saving a file.

The report binds the session, requirements, sources, requirement/test links,
current simulation, model count and review to hashes. Rehashed artifact bytes,
runner/profile bindings and exact testcase names are required for a v2 run.
Old receipts require a fresh runtime-bound run; migration cannot invent evidence.
Corruption fails closed. Pending warning IDs remain visible even after broad
delegated acceptance. Use session review commands to inspect and acknowledge
warnings; acknowledgement itself is not a test or correctness proof.

`live_qualification` always remains `not_established_by_this_report`: a forged
but self-consistent local receipt could pass the hash checks. Treat the session
directory and database as local, user-controlled artifacts, not signed attestations.
The report exposes bounded identifiers and digests, not source bodies, raw
conversation transcripts, credentials or full tool logs.

## Installed-package smoke lane

The local validation handoff builds the candidate wheel and source distribution
offline using the pinned existing build tool, installs the wheel without fetching
dependencies into a new task-owned target, and runs
`tools/verify_design_agent_install.py`. It verifies the wheel's Python/typing
payload against the copied candidate source tree, then checks that all imported
OpenRTL modules and the console entry point come from that installed target.
No editable OpenRTL source fallback is accepted.

The smoke uses fresh CLI processes for three specification review/save/resume
flows, exact approval, no-provider refusal, a batch review stop and a two-file
import. The outer interpreter's already-installed dependencies are deliberately
reused and this limitation is recorded. This is not a fresh dependency install.
Every run uses a new output directory, retains diagnostics and rejects overwriting
an existing directory. Source, wheel, source-distribution, evaluation archive and
report hashes distinguish the unreleased candidate from the published toolkit.

The companion `openrtl-design-agent-evaluation.tar.gz` includes these guides,
the smoke verifier and specification inputs. It contains no generated solutions
or live receipts. Its normalized paths, file modes and timestamps support
reproducible archive checks. No tag or release is created by building it.

## Live evaluation still required

1. Freeze the validated candidate source/package digests. Select and explicitly
   authorize a model, bounded call/token/time/repair limits, and a trusted local
   container profile. No credential value belongs in the review or report.
   No image download, SDK installation or provider call is implied by local checks.
2. Review `examples/design_acceptance/alu8.json`. Start an interactive session,
   load it with `/spec <absolute-path>`, inspect the complete specification and
   approve its displayed digest. Run the actual provider through the engineering
   stages. Review RTL, independently derived model, assertions, DV and meaningful
   acceptance checks—not just their filenames.
3. Run the resulting collateral inside the selected isolated profile. Retain
   failures, any bounded repair, tool versions, source hashes, results and waves.
   Do not edit the checker to make a failing design pass without an explicit
   reviewed DV change. Save/resume mid-workflow and verify permissions are not
   implicitly restored.
4. Require signoff, inspect unresolved findings, then separately grant final
   acceptance. Re-run the read-only acceptance inventory with the exact reviewed
   specification. If requirements changed, review the new input rather than
   calling the original scenario passed.
5. Exercise a sequential design (`counter4.json`) in explicitly delegated batch
   mode, review every assumption warning, and inspect its reset/timing checks.
   Exercise `arbiter4.json` or an additional design selected after the freeze.
   These public inputs are not blind benchmarks; record genuinely held-out inputs
   separately and do not tune the implementation to their expected answers.
6. Test imported RTL/DV explanations, a reviewed feature change, a DV-only change
   that preserves RTL/model bytes, and a simulation-level experiment. Compare
   only compatible reverified runs. Test counts and simulated durations are not
   functional coverage or hardware performance measurements.

Keep the candidate version/source hashes, input hashes, selected model/runtime
identities, call counts, failures, session revisions, acceptance output and human
adequacy findings in the evaluation record. Never retain credentials, provider
exception bodies, raw private prompts or hidden reasoning. Only after these
lanes pass should a new release identity, clean-install test and publication be
considered. See [the alpha guide](design-agent-alpha.md),
[imports](design-imports.md) and [coaching](design-coaching.md) for exact commands.
