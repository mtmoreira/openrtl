# ADR 0026: Bounded composed-package matrix

## Status

Accepted.

## Context

M33 proved one FIFO-to-skid composition at width 8, FIFO depth 4, and seed 33.
That evidence verifies dependency-closed reuse, but it does not exercise width
propagation, the FIFO's non-power-of-two pointer path, or seed diversity.

## Decision

Keep the M33 single-case command compatible and make its RTL, scoreboard, and
runner accept explicit width, depth, and seed values. Add a separate M34 matrix
runner with exactly three reviewed cases: `(4, 2, 7)`, `(8, 4, 33)`, and
`(16, 3, 91)`. Each case independently performs producer simulation, packaging,
dependency locking, source-only materialization, and consumer recompilation.

The matrix requires multiple widths and depths plus at least one
non-power-of-two depth. Every case must fill its FIFO-plus-skid capacity, drain,
preserve source bytes, and bind its own package locks, reports, and waveforms.
An aggregate manifest records all cases without replacing their evidence.

A dedicated GitHub Actions job runs the focused tests, existing real FIFO and
skid simulations, and the three-case matrix. The job uses the exact public
AgentRig commit and an explicit Verilator/cocotb toolchain; it performs no
provider calls or publication.

## Consequences

This is a bounded regression matrix, not exhaustive parameter verification or
formal proof. Depth is capped at 64 for this simulation entry point, while the
underlying reusable FIFO contract retains its broader declared range.
