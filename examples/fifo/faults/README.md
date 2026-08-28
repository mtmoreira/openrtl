# FIFO level-update fault case

This provider-free example renders a small synthetic VCD in which a second
accepted write leaves `sync_fifo.level` at one instead of advancing it to two.
The production FIFO RTL is never changed. The fixture exists to demonstrate
that OpenRTL can retain a failed debug session, propose a source- and
waveform-linked repair for review, and generate a focused Surfer command file.

Run the complete example from the repository root:

```sh
PYTHONPATH=src:../agentrig/src:. python tools/fifo_fault_case.py \
  --output-directory build/fifo-level-fault
```

Inspect the key signals in Surfer with:

```sh
surfer \
  --command-file build/fifo-level-fault/focus.sucl \
  build/fifo-level-fault/waves.vcd
```

At the 25 ns marker, inspect `wr_valid`, `wr_ready`, `write_accepted`, and
`level`. The handshake is accepted, but the post-edge level remains `1`; the
proposal therefore points to the sequential count-update lines rather than the
handshake assignments.

`sync_fifo_level_fault.sv` is the equivalent intentional RTL fixture. Run the
explicit Verilator qualification to produce failing and repaired waveforms:

```sh
uv run python tools/fifo_repair_application_case.py \
  --output-directory build/fifo-repair-application
```

The reviewed edit is written only to the build directory. The tracked fault
fixture and `examples/fifo/rtl/sync_fifo.sv` remain unchanged.
