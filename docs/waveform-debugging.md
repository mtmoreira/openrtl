# Waveform debugging

OpenRTL keeps waveform debugging reviewable: inspection is bounded JSON and a
Surfer focus is a deterministic command file (`.sucl`). Neither operation
launches a GUI unless the user explicitly selects `--launch`.

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
