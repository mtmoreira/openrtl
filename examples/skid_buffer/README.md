# Ready/valid skid buffer

This second OpenRTL example proves that the FIFO evidence workflow generalizes
to a different state machine. The correct RTL supports transparent transfers,
one-word retention under backpressure, and same-edge refill. The fault fixture
removes the refill-ready path so the waveform diagnosis can expose a throughput
bubble without changing the production source.
