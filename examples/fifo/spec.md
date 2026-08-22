# Synchronous FIFO specification

## Interface

- Parameter `WIDTH` is at least 1; `DEPTH` is at least 2.
- `clk` is the only clock.
- `rst_n` is sampled synchronously and clears all observable FIFO state when low.
- Input channel: `wr_valid`, `wr_ready`, `wr_data[WIDTH-1:0]`.
- Output channel: `rd_valid`, `rd_ready`, `rd_data[WIDTH-1:0]`.
- Status: `full`, `empty`, and `level[$clog2(DEPTH+1)-1:0]`.

## Requirements

- `fifo.reset`: after a reset edge, `empty=1`, `full=0`, `level=0`, and
  `rd_valid=0`.
- `fifo.write`: a write is accepted exactly when `wr_valid && wr_ready` at a
  rising edge; accepted data is retained until read.
- `fifo.read`: a read is accepted exactly when `rd_valid && rd_ready` at a
  rising edge; `rd_data` is the oldest retained word before that edge.
- `fifo.order`: accepted words are returned exactly once in acceptance order.
- `fifo.backpressure`: when full, writes are blocked unless a read is accepted
  on the same edge; when empty, reads are blocked.
- `fifo.simultaneous`: simultaneous accepted read and write preserve `level` and
  sustain one word per cycle after the FIFO becomes non-empty.
- `fifo.wrap`: behavior is unchanged across read/write pointer wraparound for
  both power-of-two and non-power-of-two depths.
- `fifo.status`: `empty` is equivalent to `level==0`, and `full` is equivalent
  to `level==DEPTH`.

## Verification closure

The reference-model deterministic and randomized tests cover every requirement.
The cocotb scoreboard compares every cycle with the same model, logs transfers
using the OpenRTL JSON event schema, and requests a VCD trace. Signoff requires
all model and RTL tests to pass with no internal assertion failures.
