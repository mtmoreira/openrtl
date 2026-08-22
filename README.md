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
python tools/validate.py
```

The simulator canary is separately selected because it requires Verilator and
cocotb:

```sh
python -m openrtl.cli canary --project examples/sync_fifo
```

No provider call, GUI launch, package publication, or remote Git operation is
performed by the offline validation lane.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the artifact-first context
model and [docs/development-plan.md](docs/development-plan.md) for milestone
exit criteria.
