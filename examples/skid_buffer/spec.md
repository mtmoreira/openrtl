# Ready/valid skid-buffer contract

The example is a one-entry elastic buffer between an upstream source and a
downstream sink.

- `skid.reset`: synchronous active-low reset clears retained state.
- `skid.accept`: transfers occur only on `valid && ready`.
- `skid.backpressure`: an unaccepted input word is retained while the sink is
  blocked.
- `skid.order`: every accepted word appears exactly once and in order.
- `skid.refill`: a retained word may leave while a replacement word is
  accepted on the same edge, with no throughput bubble.

The debug-visible `occupied` output is evidence state, not an additional
flow-control input.
