# Synchronous FIFO canary

This canary exercises OpenRTL V1 from requirement IDs through a Python reference
model, synthesizable SystemVerilog, inline simulation assertions, cocotb DV,
standardized JSON log events, and VCD generation.

Run the dependency-free model checks with:

```sh
python -m unittest examples.fifo.test_model
```

With Verilator and cocotb installed, run the RTL lane with:

```sh
make -C examples/fifo/dv
```

The canary uses a synchronous active-low reset. Transfers occur on rising clock
edges when `valid && ready` is true.
