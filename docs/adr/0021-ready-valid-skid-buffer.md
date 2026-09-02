# ADR 0021: Ready/valid skid-buffer example

## Status

Accepted.

## Context

The synchronous FIFO proves OpenRTL's evidence and repair workflow for
occupancy, pointers, and ordered storage. A second block is needed to prove the
workflow is not accidentally coupled to FIFO semantics. The new example must
exercise a different protocol invariant while preserving the same reviewable
debug-session and evidence contracts.

## Decision

Add a one-entry ready/valid skid buffer with a reference model, synthesizable
SystemVerilog, randomized cocotb verification, and a deterministic same-edge
refill case. Keep skid-buffer protocol checks in a block-specific adapter that
emits the existing generic debug-session schema.

Retain an intentionally broken fixture whose input-ready equation ignores a
simultaneous output transfer. At the refill edge the faulty trace deasserts
`s_ready` and loses the replacement transfer, while the production trace keeps
`s_ready` asserted and remains occupied. A non-applying repair proposal and
paired focus files bind that visible difference to exact waveform and source
anchors.

The example is included in development validation and future source packages.
It does not change the already-published OpenRTL 0.3.0 artifacts, tags, release
manifest, or immutable public-acceptance contract.

## Consequences

- Shared artifact schemas remain block-neutral; only protocol interpretation
  is specialized.
- Validation covers transparent transfer, backpressure capture, retained data,
  and same-edge dequeue/refill behavior.
- The correct production RTL is never modified by the fault or repair case.
- Further protocol examples can reuse the generic evidence boundary without
  forcing FIFO and skid-buffer rules into one analyzer.
