# Dependency-composed stream

`fifo_skid_stream` connects the existing parameterized FIFO to the existing
parameterized skid buffer. Its defaults remain width 8 and FIFO depth 4, for a
total capacity of five beats. No leaf RTL is modified.

The trusted cocotb scoreboard checks data order, accepted/delivered accounting,
configured FIFO-plus-skid capacity, stable stalled output, backpressure,
simultaneous transfer, reset with outstanding data, and final drain. Width,
depth, and seed are explicit and recorded.

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
checked-in trusted harness. `tools/composed_package_matrix.py` runs the reviewed
4×2×7, 8×4×33, and 16×3×91 configurations independently and emits an aggregate
manifest. This bounded matrix is not an exhaustive parameter proof and does
not change any published release archive.
