# ADR 0004: Make waveform diagnosis an evidence-linked debug session

Status: accepted

OpenRTL diagnoses synchronous-FIFO traces by sampling handshake inputs
immediately before each rising clock edge and state immediately after that
edge. This preserves SystemVerilog nonblocking-assignment semantics and avoids
mistaking post-edge combinational changes for the values accepted by the RTL.

The deterministic analyzer checks valid/ready acceptance, full read-through
backpressure, occupancy updates, status flags, pointer enables and wraparound,
and retained-word ordering. It emits an immutable debug-session report with the
trace digest, exact waveform interval, clock-edge markers, requirement IDs,
relevant RTL source anchors, observations, findings, and bounded next probes.
The report contains no hidden reasoning and can be reviewed without launching
a GUI.

The FIFO analyzer is an OpenRTL adapter over the dependency-free VCD index.
Generic debug observations, findings, and reports remain application contracts
so future protocol- and block-specific analyzers can contribute evidence in the
same shape. A finding is a diagnostic artifact, not an automatic RTL mutation;
repair still passes through review and deterministic validation.
