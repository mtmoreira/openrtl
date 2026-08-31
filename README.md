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

## V1 toolchain

- SystemVerilog
- Verilator
- cocotb 2.0.1
- VCD traces
- Surfer viewer requests and command files

FPGA synthesis, formal execution, device programming, and remote community
publication are deferred behind explicit ports.

## Development

Run the provider-free validation lane:

```sh
PYTHONPATH=src:../agentrig/src python -m unittest discover -s tests -t .
PYTHONPATH=src:../agentrig/src:. python -m unittest examples.fifo.test_model
python tools/validate.py
```

The default validation lane never invokes the external simulator and prints a
`verilator_cocotb_canary not_selected` checkpoint. Select the installed
Verilator/cocotb toolchain explicitly with:

```sh
uv sync --locked --extra simulation
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

Exact executable overrides are available when PATH selection is insufficient:

```sh
uv run python tools/validate.py --with-verilator \
  --verilator-executable /absolute/path/to/verilator
```

No provider call, GUI launch, package publication, or remote Git operation is
performed by either validation lane.

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
  --source examples/fifo/faults/sync_fifo_level_fault.sv \
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
  --source examples/fifo/faults/sync_fifo_level_fault.sv \
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
  --source examples/fifo/faults/sync_fifo_level_fault.sv \
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
  --source examples/fifo/faults/sync_fifo_level_fault.sv \
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
  --source examples/fifo/faults/sync_fifo_level_fault.sv \
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
Production promotion is intentionally a separate, future explicit operation.

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

## Architecture

See [docs/architecture.md](docs/architecture.md) for the artifact-first context
model and [docs/development-plan.md](docs/development-plan.md) for milestone
exit criteria.
