# Synchronous FIFO canary

This canary exercises OpenRTL V1 from requirement IDs through a Python reference
model, synthesizable SystemVerilog, inline simulation assertions, cocotb DV,
standardized JSON log events, and VCD generation.

Run the dependency-free model checks with:

```sh
python -m unittest examples.fifo.test_model
```

With Verilator and the simulation extra installed, explicitly select the
artifact-preserving RTL lane with:

```sh
uv run python tools/validate.py --with-verilator
```

The verified log, results XML, VCD trace, and simulator build are retained under
`build/verilator-fifo-canary/`. The default `python tools/validate.py` command
does not invoke Verilator.

The canary uses a synchronous active-low reset. Transfers occur on rising clock
edges when `valid && ready` is true.
