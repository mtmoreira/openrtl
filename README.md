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
results XML, VCD trace, and simulator build under
`build/verilator-fifo-canary/`. Exact executable overrides are available when
PATH selection is insufficient:

```sh
uv run python tools/validate.py --with-verilator \
  --verilator-executable /absolute/path/to/verilator
```

No provider call, GUI launch, package publication, or remote Git operation is
performed by either validation lane.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the artifact-first context
model and [docs/development-plan.md](docs/development-plan.md) for milestone
exit criteria.
