# Dependency-composed stream

`fifo_skid_stream` connects the existing 8-bit, depth-4 FIFO to the existing
8-bit skid buffer. It holds five beats in total. No leaf RTL is modified.

The trusted cocotb scoreboard checks data order, accepted/delivered accounting,
five-beat occupancy, stable stalled output, backpressure, simultaneous transfer,
reset with outstanding data, and final drain. Seed 33 is fixed and recorded.

Run `tools/composed_package_case.py` explicitly with passing FIFO/skid evidence
from `tools/validate.py --with-verilator`, an absent output directory, and exact
toolchain paths. The tool verifies both leaf candidates, runs the composed
producer, stores three packages and their exact dependency pins, and locks them.
It deletes only its own three producer RTL copies after verifying their bytes,
materializes the closure, and runs the same trusted testbench against only the
three consumer RTL files. The repository's original sources remain unchanged.

The consumer run's compile argv, environment policy, source hashes, coverage,
results, and waveform are retained. This is a source-path-isolated build, not
an OS sandbox: the filesystem remains accessible, but no producer source path
or repository Python import path is supplied to the consumer simulator.
Package testbenches and install hooks are not executed; the runner uses the
checked-in trusted harness. The fixed parameters are not a claim about other
widths or depths. This lane does not change any published release archive.
