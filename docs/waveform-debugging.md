# Waveform debugging

OpenRTL keeps waveform debugging reviewable: inspection is bounded JSON and a
Surfer focus is a deterministic command file (`.sucl`). Neither operation
launches a GUI unless the user explicitly selects `--launch`. With Surfer 0.7,
the command file adds the selected signals; its comments and inspection JSON
retain the exact integer-fs window and markers for manual viewport placement.

## Inspect the FIFO trace

List every signal captured by the retained Verilator canary:

```sh
uv run openrtl waveform inspect \
  build/verilator-fifo-canary/waves.vcd \
  --root .
```

Inspect handshake and occupancy transitions in a time window:

```sh
uv run openrtl waveform inspect \
  build/verilator-fifo-canary/waves.vcd \
  --root . \
  --signal sync_fifo.wr_valid \
  --signal sync_fifo.wr_ready \
  --signal sync_fifo.rd_valid \
  --signal sync_fifo.rd_ready \
  --signal sync_fifo.level \
  --start-fs 0 \
  --end-fs 4001000000 \
  --max-transitions 200 \
  --output build/waveform-debug/inspection.json
```

The report includes the value at the start of the window, bounded transitions,
and a `truncated` flag for each selected signal. Trace files must be regular,
non-symlinked, no larger than 64 MiB, and contained by `--root`.

## Signals to put on screen

For the synchronous FIFO, start with this order:

1. `sync_fifo.clk` and `sync_fifo.rst_n` establish sampling and reset.
2. `sync_fifo.wr_valid` and `sync_fifo.wr_ready` show accepted writes when both
   are high on a rising clock edge; add `sync_fifo.wr_data` below them.
3. `sync_fifo.rd_valid` and `sync_fifo.rd_ready` show accepted reads; add
   `sync_fifo.rd_data` below them.
4. `sync_fifo.level`, `sync_fifo.full`, and `sync_fifo.empty` explain
   backpressure and occupancy.
5. `sync_fifo.write_pointer` and `sync_fifo.read_pointer` expose wraparound and
   are the next signals to add when ordering or boundary behavior is suspect.

At every rising edge, check the handshake pairs first. A write-only transfer
increments `level`, a read-only transfer decrements it, and simultaneous
accepted read/write transfers leave it unchanged. Data accepted at the write
side must later appear at the read side in order.

## Generate an evidence-linked FIFO diagnosis

Ask OpenRTL to apply those checks deterministically across a bounded window:

```sh
uv run openrtl waveform diagnose-fifo \
  build/verilator-fifo-canary/waves.vcd \
  --root . \
  --start-fs 100000000 \
  --end-fs 220000000 \
  --output build/waveform-debug/diagnosis.json
```

The command samples inputs and acceptance signals immediately before each
rising edge, then samples occupancy, status, pointers, and output state after
the edge. Its `openrtl.debug-session.v1` JSON contains:

- an immutable trace digest and bounded waveform anchor;
- exact rising-edge markers and relevant signal names;
- one requirement-linked observation per sampled edge;
- digest-bound RTL anchors for handshake and state-update logic;
- fail-closed findings with expected and observed values plus the next probe.

A clean report has `"passed": true` and an empty `findings` list. A detected
invariant violation is still written to the selected output, and the CLI
returns a nonzero status so automation cannot mistake a diagnosis for a pass.
The report does not alter RTL or launch Surfer.

## Review a repair proposal for a failing trace

Generate a retained debug session, non-applying proposal, and narrow Surfer
focus together:

```sh
uv run openrtl waveform propose-fifo-repair \
  build/failing-run/waves.vcd \
  --root . \
  --start-fs 24000000 \
  --end-fs 26000000 \
  --output-directory build/fifo-repair-proposal
```

Review `debug-session.json` first: confirm the expected and observed values at
each marker. Then review `repair-proposal.json`: every change names its covered
finding and requirement and repeats only anchors already established by the
debug session. The proposal has `applies_changes: false`; it is evidence for a
later engineering decision, not permission to edit RTL.

For the included level-update fault case, open the generated focus:

```sh
uv run python tools/fifo_fault_case.py \
  --output-directory build/fifo-level-fault
surfer \
  --command-file build/fifo-level-fault/focus.sucl \
  build/fifo-level-fault/waves.vcd
```

At 25 ns, inspect these signals in order:

1. `sync_fifo.wr_valid`, `sync_fifo.wr_ready`, and
   `sync_fifo.write_accepted` establish that the write was accepted.
2. `sync_fifo.level` should change from `1` to `2`, but the fault trace leaves
   it at `1`.
3. `sync_fifo.read_accepted` confirms that no simultaneous read explains the
   unchanged occupancy.

The proposal consequently targets the sequential state-update anchors, not the
valid/ready combinational assignments.

Surfer 0.7 does not accept OpenRTL's former `zoom_to`, time-qualified
`cursor_set`, or `marker_set_at` batch syntax. After the signals load, zoom
manually to 24–26 ns and place the cursor at 25 ns. Those exact values are also
recorded as `focus-window-fs` and `focus-markers-fs` comments in `focus.sucl`.

## Compare a reviewed repair before and after

The opt-in repair qualification runs an actual faulty RTL fixture, prepares a
provider-neutral Diagnosis and Closure Engineer request, ingests the reviewable
example specification as a synthetic strict expert response, and retains an
untrusted `awaiting_qualification` report. It then builds a typed edit plan,
applies the digest-approved plan to a separate candidate, and reruns the same
deterministic cocotb test:

```sh
uv run python tools/fifo_repair_application_case.py \
  --output-directory build/fifo-repair-application
```

Open `before/waves.vcd` with `focus-before.sucl`, then
`repaired/waves.vcd` with `focus-after.sucl`. Both traces deliberately continue
through a later clock transition, and both focus files keep the finding strictly
inside a padded pre/post-edge window. At the accepted-write edge, compare these
signals in order:

1. `sync_fifo.clk` establishes the sampling edge;
2. `sync_fifo.wr_valid`, `sync_fifo.wr_ready`, and
   `sync_fifo.write_accepted` are `1` in both runs;
3. `sync_fifo.rd_valid`, `sync_fifo.rd_ready`, and
   `sync_fifo.read_accepted` exclude a simultaneous read;
4. `sync_fifo.level` remains `0` across the post-edge interval in the failing
   run and remains `1` across that interval in the repaired run;
5. `sync_fifo.empty` stays asserted only in the failing run.

Surfer 0.7 adds these signals but does not apply the recorded viewport. Read the
`focus-window-fs` comment and zoom manually so that time remains visible on both
sides of `focus-markers-fs`. The generated `comparison.json` independently
requires each trace to extend beyond that window, a later clock transition to
be present, and the differing `level` values to persist through the focus end.

Inspect `expert-edit-request.json`, `expert-edit-response.json`, and
`expert-edit-suggestion.json` first. Confirm that the context, proposal,
failed-session, source, and ordered change bindings match and that the
suggestion status is `awaiting_qualification`. Then inspect `edit-plan.json`
before `application.json`: its exact expected and
replacement bytes, ranges, source digest, and canonical digest are the approval
boundary. The retained `comparison.json` requires the original linked finding,
an empty repaired finding list, and `visual_evidence.status` equal to
`visibly_distinct`. `evidence.json` binds every log, results file, waveform,
focus, proposal, edit plan, application report, and repaired source by SHA-256.
The workflow makes no provider call, does not launch Surfer, and does not modify
production RTL.

When the controlled expert-invocation lane is used, inspect
`invocation-envelope.json` before the response. Its `diagnosis` section contains
the exact waveform findings and bounded edge observations shown in the Surfer
focus, while `source.excerpts` contains only the digest-bound lines named by the
proposal. Confirm `runtime.tool_ids` is empty, `max_turns` is one, and the
invocation report still says `awaiting_qualification`; the model output is not
an approval or a repair application.

For an explicitly selected provider, review the provider plan before granting
the exact digest. Confirm its request digest identifies the same failed trace,
its runtime exposes no tools, and its source excerpts cover the signals in the
Surfer focus. After the call, compare the invocation envelope to this trace and
require both lifecycle reports to remain `awaiting_qualification`. A provider
response does not change either waveform and cannot apply the proposed edit.

Before reviewing provider-produced edit bytes, inspect
`provider-output-qualification.json`. Its lineage must identify the exact
provider plan, execution receipt, invocation, suggestion, and edit-spec
digests, while its edit-plan and planning bindings identify the deterministic
qualification outputs. The receipt must remain `awaiting_review`,
`provider_output_trusted` must remain false, and `applies_changes` must remain
false. Then inspect the same causal waveform signals above; provenance
qualification does not replace the visible before/repaired evidence.

After explicit review, retain `qualified-provider-application.json` beside the
generic application report. It binds the candidate write to the exact provider
qualification and approval digests. The receipt does not replace renewed
simulation: the before trace must retain the finding and the repaired candidate
trace must remove it.

Surfer 0.7 adds the listed variables but does not position the cursor from
OpenRTL's focus comments. Open the exact `before/waves.vcd` or
`repaired/waves.vcd` path and manually select 10 ns (10,000 ps). Both windows
otherwise display the same `waves.vcd` title. At that edge the faulty FIFO keeps
`level` at zero while the repaired candidate changes it to one.

Once that difference is confirmed, inspect `promotion-plan.json`. Its candidate
and target digests must match the exact files being reviewed, and its validation
bindings must identify this comparison, repaired results, and repaired waveform.
The status is `awaiting_promotion_approval`; generating it does not replace the
fault source or any production RTL.

After independent signoff, `repair promote-qualified-provider-candidate`
rehashes the exact plan, candidate, and target before changing the target. Its
receipt must show `promoted_to_production` and a final target digest identical
to the candidate digest. This operation does not call a provider or alter the
separately named failing regression fixture.

## Prepare and open a Surfer focus

Generate reusable inspection and viewer state:

```sh
uv run openrtl waveform focus \
  build/verilator-fifo-canary/waves.vcd \
  --root . \
  --signal sync_fifo.clk \
  --signal sync_fifo.wr_valid \
  --signal sync_fifo.wr_ready \
  --signal sync_fifo.wr_data \
  --signal sync_fifo.rd_valid \
  --signal sync_fifo.rd_ready \
  --signal sync_fifo.rd_data \
  --signal sync_fifo.level \
  --output-directory build/waveform-debug
```

Open the result yourself:

```sh
surfer \
  --command-file build/waveform-debug/focus.sucl \
  build/verilator-fifo-canary/waves.vcd
```

Read `inspection.json` or the leading comments in `focus.sucl`, then set that
window in the Surfer UI. The command file intentionally uses only
`variable_add`, the command verified against Surfer 0.7.

Or explicitly authorize OpenRTL to start the selected local executable:

```sh
uv run openrtl waveform focus \
  build/verilator-fifo-canary/waves.vcd \
  --root . \
  --signal sync_fifo.wr_valid \
  --signal sync_fifo.rd_valid \
  --signal sync_fifo.level \
  --output-directory build/waveform-debug \
  --surfer-executable /absolute/path/to/surfer \
  --launch
```

The launch tool starts a detached process with exact arguments, an empty
environment, no shell, and no captured GUI output. Validation never selects it.
