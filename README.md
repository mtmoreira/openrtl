# OpenRTL

OpenRTL is an evidence-driven AI assistant for designing and verifying RTL with
a team of explicitly configured expert agents. Version 1 targets simulation
only: it turns requirements into traceable specifications, plans, reference
models, synthesizable SystemVerilog, assertions, DV collateral, simulations,
diagnostics, reviews, and guided learning sessions.

The product has two conversation policies over one engineering workflow:

- **Build mode** develops a block autonomously through bounded, reviewable
  stages.
- **Learn mode** pauses at teaching checkpoints and links explanations to exact
  requirements, source lines, log events, and waveform windows.

OpenRTL uses AgentRig for portable agent/runtime/tool contracts. OpenRTL owns
the hardware-design schemas, artifact graph, evidence, EDA adapters, reuse
catalog, and convergence rules.

The current release is [OpenRTL 0.3.0](https://github.com/mtmoreira/openrtl/releases/tag/v0.3.0). Its checked release bundle
contains a library-only wheel, a source distribution, and a deterministic
companion archive with the complete FIFO model, RTL, DV, waveform, fault, and
repair examples. See [docs/releases.md](docs/releases.md) for the immutable
artifact and clean-install verification contract.

OpenRTL 0.3.0 pins the published AgentRig 0.3.0 contract. Its public acceptance
lane verifies both annotated tags and exact commits, downloads and hashes the
published artifacts, then installs and runs the released examples in isolation:

```sh
python tools/validate_public_release_v030.py \
  --output-directory build/public-release-v0.3.0-acceptance \
  --with-verilator
```

The immutable OpenRTL 0.2.0 acceptance command remains available as a separate
historical contract in `tools/validate_public_release.py`. See
[docs/releases.md](docs/releases.md) for both release identities.

Post-0.3 development also includes a one-entry ready/valid skid buffer as a
second protocol proof. Its reference model, correct RTL, randomized cocotb
test, deterministic refill fault, diagnosis, non-applying proposal, and visibly
distinct before/after waveforms are development artifacts; they do not alter
the published 0.3.0 release bundle.

This command performs public GitHub reads and local package installation only.
It does not invoke a provider or modify either repository.

## V1 toolchain

- SystemVerilog
- Verilator
- cocotb 2.0.1
- VCD traces
- Surfer viewer requests and command files

FPGA synthesis, formal execution, device programming, and remote community
publication are deferred behind explicit ports.

## Development

Keep an exact AgentRig 0.3.0 checkout at the sibling path selected by
`tool.uv.sources`, then run the provider-free validation lane:

```sh
uv sync --locked --extra simulation
uv run python -m unittest discover -s tests -t .
uv run python -m unittest examples.fifo.test_model
uv run python -m unittest examples.skid_buffer.test_model
uv run python tools/validate.py
```

The default validation lane never invokes the external simulator and prints a
`verilator_cocotb_canary not_selected` checkpoint. Select the installed
Verilator/cocotb toolchain explicitly with:

```sh
uv run python tools/validate.py --with-verilator
```

The opt-in lane resolves and prints the exact `verilator`, `make`, and
`cocotb-config` executables, applies a bounded timeout, and retains the run log,
results XML, VCD trace, simulator build, and a hash-bound evidence manifest
under `build/verilator-fifo-canary/`. The manifest is accepted only when every
referenced file is contained, regular, unchanged, bounded, and semantically a
passing FIFO run. Build or learn mode can then derive package candidacy from
that verified run:

```sh
uv run openrtl verified-canary --root . --mode build
uv run openrtl verified-canary --root . --mode learn
```

The compatibility command above remains available for the published FIFO
walkthrough. New designs use checked-in, block-neutral simulation profiles.
Each profile declares its package interface, source set, requirements, passing
test identity, retained artifact keys, and waveform focus; Python only verifies
the declared contract. Build FIFO and skid-buffer candidates into one local
catalog after their evidence manifests exist:

```sh
uv run openrtl verified-package --root . \
  --profile examples/fifo/verified-profile.json \
  --manifest build/verilator-fifo-canary/evidence.json \
  --catalog-root build/design-catalog
uv run openrtl verified-package --root . \
  --profile examples/skid_buffer/verified-profile.json \
  --manifest build/skid-buffer-case/evidence.json \
  --catalog-root build/design-catalog
```

Profile, source, requirement, result, or waveform mixing fails closed. Catalog
storage is local and refuses to overwrite an existing package version; it does
not publish anything remotely.

For a catalog entry that remains consumable after the producing build tree is
removed, store a self-contained bundle and retain the returned manifest digest:

```sh
uv run openrtl portable-package --root . \
  --profile examples/skid_buffer/verified-profile.json \
  --manifest build/skid-buffer-case/evidence.json \
  --catalog-root build/portable-catalog
```

Consumption requires that digest, rehashes every bundled source and evidence
file, reconstructs the typed package, checks the requested interface and
parameters, then copies only package source files into a new destination:

```sh
uv run openrtl materialize-package \
  --catalog-root build/portable-catalog \
  --package-id community.ready-valid.skid-buffer \
  --version 1.0.0 \
  --expected-manifest-digest sha256:REVIEWED_MANIFEST_DIGEST \
  --destination build/consumer/skid-buffer \
  --require-port s_ready:output:1 \
  --parameter width=8
```

Materialization never executes package files or install hooks. Existing
destinations, incompatible interfaces, stale digests, missing payloads,
symlinks, and modified bytes are rejected before the destination appears.

For a package with dependencies, pin every bundle manifest and resolve an exact
dependency closure before materialization:

```sh
uv run openrtl lock-package-closure \
  --catalog-root build/portable-catalog \
  --root-package-id community.example.system \
  --root-version 1.0.0 \
  --bundle-pin community.example.system@1.0.0=sha256:ROOT_MANIFEST_DIGEST \
  --bundle-pin community.sync.fifo@1.0.0=sha256:FIFO_MANIFEST_DIGEST \
  --output build/package-closure.lock.json
```

The returned lock digest is required to consume the closure:

```sh
uv run openrtl materialize-package-closure \
  --catalog-root build/portable-catalog \
  --lock build/package-closure.lock.json \
  --expected-lock-digest sha256:REVIEWED_LOCK_DIGEST \
  --destination build/consumer/system \
  --require-port ready:output:1 \
  --parameter width=8
```

Resolution rejects missing or unused pins, version or digest drift, duplicate
identities, and dependency cycles. Materialization reverifies every bundle and
writes the complete dependency-first workspace atomically.

Exact executable overrides are available when PATH selection is insufficient:

```sh
uv run python tools/validate.py --with-verilator \
  --verilator-executable /absolute/path/to/verilator
```

With `--with-verilator`, validation runs both the production FIFO canary and
the skid-buffer refill case. The latter requires one failing fault trace, one
passing production trace, and a visible `s_ready`/occupancy difference at the
same marker. No provider call, GUI launch, package publication, or remote Git
operation is performed by either validation lane.

Diagnose a retained skid-buffer trace without changing RTL:

```sh
uv run openrtl waveform diagnose-skid-buffer \
  build/skid-buffer-case/before/waves.vcd \
  --root . \
  --start-fs 0 \
  --end-fs 40000000 \
  --output build/skid-buffer-debug/diagnosis.json
```

Generate the same diagnosis together with a non-applying repair proposal and
Surfer focus:

```sh
uv run openrtl waveform propose-skid-buffer-repair \
  build/skid-buffer-case/before/waves.vcd \
  --root . \
  --start-fs 0 \
  --end-fs 40000000 \
  --output-directory build/skid-buffer-repair-proposal
```

Use the bounded waveform workbench to list signals, inspect transitions, and
prepare deterministic Surfer focus state. See
[docs/waveform-debugging.md](docs/waveform-debugging.md) for the FIFO signal
walkthrough and explicit viewer-launch command. Surfer 0.7 command files add
the selected signals and retain the exact focus window and markers as comments;
viewport placement remains a manual viewer action.

Turn a retained FIFO trace into a reviewable, evidence-linked debug session:

```sh
uv run openrtl waveform diagnose-fifo \
  build/verilator-fifo-canary/waves.vcd \
  --root . \
  --start-fs 100000000 \
  --end-fs 220000000 \
  --output build/waveform-debug/diagnosis.json
```

The report explains every rising-edge transfer, links observations to FIFO
requirements, binds the VCD and relevant RTL lines by digest, and flags
handshake, backpressure, occupancy, status, pointer, wraparound, or ordering
violations. It never launches the viewer or edits RTL.

Turn a failing FIFO trace into a reviewable, non-applying repair proposal and
a focused Surfer command file:

```sh
uv run openrtl waveform propose-fifo-repair \
  build/failing-run/waves.vcd \
  --root . \
  --start-fs 24000000 \
  --end-fs 26000000 \
  --output-directory build/fifo-repair-proposal
```

Prepare an exact, provider-neutral request for source-edit suggestions:

```console
uv run openrtl repair prepare-expert-source-edits \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --source examples/fifo/faults/sync_fifo_level_fault_fixture.sv \
  --request-output build/fifo-repair-application/expert-edit-request.json
```

OpenRTL does not invoke a provider here. After an explicitly selected runtime
returns the declared strict schema, ingest its response as an untrusted,
non-applying specification:

```console
uv run openrtl repair accept-expert-source-edits \
  --request build/fifo-repair-application/expert-edit-request.json \
  --response build/fifo-repair-application/expert-edit-response.json \
  --edit-spec-output build/fifo-repair-application/expert-edit-spec.json \
  --suggestion-report build/fifo-repair-application/expert-edit-suggestion.json
```

Exercise the complete invocation boundary without a provider by selecting the
scripted runtime explicitly:

```console
uv run openrtl repair invoke-expert-source-edits \
  --request build/fifo-repair-application/expert-edit-request.json \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --source examples/fifo/faults/sync_fifo_level_fault_fixture.sv \
  --scripted-response build/fifo-repair-application/scripted-response.json \
  --envelope-output build/fifo-repair-application/invocation-envelope.json \
  --response-output build/fifo-repair-application/expert-edit-response.json \
  --edit-spec-output build/fifo-repair-application/expert-edit-spec.json \
  --suggestion-report build/fifo-repair-application/expert-edit-suggestion.json \
  --invocation-report build/fifo-repair-application/invocation-report.json
```

This is one tool-free AgentRig structured-generation turn with exact scripted
runtime/model identity, bounded context excerpts, deadline and output limits,
and `not_retained` data handling. It never resolves credentials or falls back
to a live provider.

Prepare—but do not execute—an exact OpenAI Responses plan:

```console
uv run openrtl repair plan-expert-provider-invocation \
  --request build/fifo-repair-application/expert-edit-request.json \
  --plan-output build/fifo-repair-application/provider-plan.json \
  --model YOUR_EXPLICIT_MODEL_ID \
  --credential-environment OPENAI_API_KEY
```

Review the plan and its `content_digest`. A real call requires an environment
that already contains AgentRig's pinned OpenAI SDK extra; OpenRTL never installs
it implicitly. Execution then requires all three explicit selections: the
provider-specific command, `--with-openai-provider`, and the exact reviewed
plan digest:

```console
uv run openrtl repair invoke-openai-expert-source-edits \
  --request build/fifo-repair-application/expert-edit-request.json \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --source examples/fifo/faults/sync_fifo_level_fault_fixture.sv \
  --plan build/fifo-repair-application/provider-plan.json \
  --with-openai-provider \
  --approve-provider-plan-digest sha256:REVIEWED_PLAN_DIGEST \
  --review-note "Reviewed exact bounded provider plan" \
  --envelope-output build/fifo-repair-application/provider-envelope.json \
  --response-output build/fifo-repair-application/provider-response.json \
  --edit-spec-output build/fifo-repair-application/provider-edit-spec.json \
  --suggestion-report build/fifo-repair-application/provider-suggestion.json \
  --invocation-report build/fifo-repair-application/provider-invocation.json \
  --provider-execution-report build/fifo-repair-application/provider-execution.json
```

Planning never reads the named credential. Execution resolves it only after
the plan digest, request, evidence, source, capability, tool, model, retention,
and resource bounds pass. The response is still untrusted and
`awaiting_qualification`; the call cannot edit RTL.

That report is only `awaiting_qualification`. Next qualify the external
exact-replacement specification into a typed, review-required edit plan:

```console
uv run openrtl repair draft-source-edits \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --source examples/fifo/faults/sync_fifo_level_fault_fixture.sv \
  --edit-spec build/fifo-repair-application/expert-edit-spec.json \
  --edit-plan-output build/fifo-repair-application/edit-plan.json \
  --planning-report build/fifo-repair-application/edit-plan-planning.json
```

The deterministic qualification command checks the external specification
against the proposal evidence and writes an `awaiting_review` report. Neither
the expert-output gate nor qualification applies or approves the plan. After
reviewing those artifacts, application remains a separate explicit command:

For provider-produced output, preserve and verify the complete provider
lineage instead of qualifying the edit specification alone:

```console
uv run openrtl repair qualify-provider-source-edits \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --source examples/fifo/faults/sync_fifo_level_fault_fixture.sv \
  --provider-plan build/fifo-repair-application/provider-plan.json \
  --provider-execution-report build/fifo-repair-application/provider-execution.json \
  --invocation-report build/fifo-repair-application/provider-invocation.json \
  --suggestion-report build/fifo-repair-application/provider-suggestion.json \
  --edit-spec build/fifo-repair-application/provider-edit-spec.json \
  --edit-plan-output build/fifo-repair-application/edit-plan.json \
  --planning-report build/fifo-repair-application/edit-plan-planning.json \
  --qualification-report build/fifo-repair-application/provider-qualification.json
```

This command makes no provider call and reads no credential. It proves that
the plan, one-call execution receipt, invocation, suggestion, edit bytes,
proposal, failed session, source, and deterministic edit plan form one exact
chain. Its result is still non-applying and `awaiting_review`.

For a provider-qualified plan, bind the human review to the exact qualification
and edit-plan digests before creating a candidate:

```console
uv run openrtl repair apply-qualified-provider-source-edits \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --edit-plan build/fifo-repair-application/edit-plan.json \
  --planning-report build/fifo-repair-application/edit-plan-planning.json \
  --qualification-report build/fifo-repair-application/provider-qualification.json \
  --output build/fifo-repair-application/candidate/sync_fifo.sv \
  --application-report build/fifo-repair-application/application.json \
  --qualified-application-report build/fifo-repair-application/qualified-application.json \
  --approve-qualification REPAIR_PROVIDER_QUALIFICATION_ID \
  --approve-qualification-digest SHA256_QUALIFICATION_DIGEST \
  --approve-proposal REPAIR_PROPOSAL_ID \
  --approve-change repair.change.level \
  --approve-edit-plan-digest SHA256_EDIT_PLAN_DIGEST \
  --review-note "Reviewed exact provider qualification and candidate-only edit."
```

This path reconstructs the qualification, planning report, and edit plan before
delegating to the generic exact-byte engine. It writes only the separate
candidate and two reviewable receipts. The original RTL remains unchanged.

After renewed simulation, bind the candidate, current target, receipts, visible
comparison, and evidence into one non-applying promotion review artifact:

```console
uv run openrtl repair plan-qualified-provider-candidate-promotion \
  --qualification-report build/fifo-repair-application/provider-output-qualification.json \
  --application-report build/fifo-repair-application/application.json \
  --qualified-application-report build/fifo-repair-application/qualified-provider-application.json \
  --candidate build/fifo-repair-application/candidate/sync_fifo.sv \
  --target-source examples/fifo/faults/sync_fifo_level_fault.sv \
  --comparison build/fifo-repair-application/comparison.json \
  --evidence build/fifo-repair-application/evidence.json \
  --promotion-plan-output build/fifo-repair-application/promotion-plan.json
```

The plan remains `awaiting_promotion_approval` and cannot modify either source.
After independent review, promotion requires every exact plan and byte binding:

```console
uv run openrtl repair promote-qualified-provider-candidate \
  --promotion-plan build/fifo-repair-application/promotion-plan.json \
  --candidate build/fifo-repair-application/candidate/sync_fifo.sv \
  --target-source examples/fifo/faults/sync_fifo_level_fault.sv \
  --promotion-receipt-output build/fifo-repair-application/promotion-receipt.json \
  --approve-promotion-plan-id REPAIR_PROMOTION_PLAN_ID \
  --approve-promotion-plan-digest SHA256_PROMOTION_PLAN_DIGEST \
  --approve-target-path examples/fifo/faults/sync_fifo_level_fault.sv \
  --approve-target-digest SHA256_TARGET_DIGEST \
  --approve-candidate-digest SHA256_CANDIDATE_DIGEST \
  --signoff-note "Independently reviewed exact candidate and renewed evidence."
```

The command reconstructs the canonical plan, rehashes both source files, writes
the target atomically, verifies that its final digest equals the candidate, and
emits a separate receipt. It never calls a provider or resolves credentials.

```console
uv run openrtl repair apply-source-edits \
  --proposal build/fifo-repair-application/proposal.json \
  --debug-session build/fifo-repair-application/debug-session.json \
  --edit-plan build/fifo-repair-application/edit-plan.json \
  --output build/fifo-repair-application/candidate/sync_fifo.sv \
  --application-report build/fifo-repair-application/application.json \
  --approve-proposal REPAIR_PROPOSAL_ID \
  --approve-change repair.change.level \
  --approve-edit-plan-digest SHA256_EDIT_PLAN_DIGEST \
  --review-note "Reviewed the linked edge and exact source anchors."
```

The commands fail on stale context, evidence, source, edit bytes, anchors, runtime
identity, model identity, data-retention drift, tool exposure, or approval
and never edits its input. Concrete repair text is carried by the reviewed edit
plan, not hardcoded in Python. The opt-in
`tools/fifo_repair_application_case.py` provider-free qualification uses the
external FIFO edit fixture as a synthetic strict expert response and retains
the request, response, untrusted suggestion, qualified edit plan,
failing and repaired Verilator waveforms, and their hash-bound comparison. Both
traces extend beyond the finding edge, and qualification rejects focus collateral
that does not expose a persistent, visually distinct post-edge level.

The proposal is hash-bound to the retained debug session and covers every
finding with matching requirement, source, and waveform anchors. It never
applies an edit. Run the deterministic demonstration without modifying the
production FIFO:

```sh
uv run python tools/fifo_fault_case.py \
  --output-directory build/fifo-level-fault
```

## Real dependency-composed simulation

The repository-only [FIFO/skid-buffer example](examples/composed_stream/README.md)
packages two independently verified leaves plus a simulated wrapper, locks their
exact dependencies, and recompiles the materialized closure with a trusted
end-to-end scoreboard. It retains producer and consumer coverage/results/VCDs;
the original leaf RTL and published release archives are unchanged.

After `python tools/validate.py --with-verilator` has produced passing leaf
evidence, run this explicitly with an **absent** output directory:

```sh
python tools/composed_package_case.py \
  --output-directory build/composed-package-case \
  --fifo-evidence build/verilator-fifo-canary/evidence.json \
  --skid-evidence build/verilator-skid-buffer-case/evidence.json \
  --verilator-executable /absolute/path/to/verilator \
  --make-executable /absolute/path/to/make \
  --cocotb-config-executable /absolute/path/to/cocotb-config
```

Parameters are fixed at WIDTH=8 and DEPTH=4. Package hooks and package-supplied
testbenches are not executed; the checked-in harness is the explicit trusted
execution input. Consumer source-path isolation is not an OS sandbox.

## Architecture references

See [docs/architecture.md](docs/architecture.md) for the artifact-first context
model and [docs/development-plan.md](docs/development-plan.md) for milestone
exit criteria.
