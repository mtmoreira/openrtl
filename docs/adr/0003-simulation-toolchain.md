# ADR 0003: Select Verilator, cocotb, VCD, and Surfer for V1

Status: accepted

V1 uses Verilator for open-source RTL simulation, cocotb for Python DV, VCD for
portable traces, and Surfer command files for bounded waveform focus. Adapters
remain replaceable so formal, commercial EDA, and alternate viewers can be
integrated later.

Repository validation does not invoke this external toolchain by default. The
explicit Verilator validation option resolves the selected executable paths,
runs with exact arguments and a bounded timeout, and retains the log, results
XML, VCD trace, and simulator build under the ignored build root. It never
launches the waveform viewer automatically.
