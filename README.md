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
walkthrough and explicit viewer-launch command.

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
