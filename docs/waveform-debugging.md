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
