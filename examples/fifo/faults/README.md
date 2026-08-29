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

`level_update_edit_spec.json` supplies the synthetic expert's concrete output;
it is reviewable example input, not trusted code. The tool first creates an
exact provider-neutral request and ingests a strict synthetic response as an
`awaiting_qualification` specification. The deterministic planner then creates
`edit-plan.json`, pins the source and every edit by SHA-256, and leaves the plan
`awaiting_review`. The generic Python engine contains no FIFO statement
replacement. The candidate is written only after exact approval and only to
the build directory; the tracked fault fixture and production FIFO remain
unchanged.
