# ADR 0003: Select Verilator, cocotb, VCD, and Surfer for V1

Status: accepted

V1 uses Verilator for open-source RTL simulation, cocotb for Python DV, VCD for
portable traces, and Surfer command files for bounded waveform focus. Adapters
remain replaceable so formal, commercial EDA, and alternate viewers can be
integrated later.
