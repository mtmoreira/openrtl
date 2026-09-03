# ADR 0025: Simulate a materialized dependency closure

## Decision

Add an explicit example runner for an 8-bit, depth-4 FIFO followed by an 8-bit
skid buffer. Reuse unchanged leaf RTL and the M30 verified candidates, M31
portable catalog, and M32 exact dependency lock/materialization contracts.
Keep this orchestration in a trusted example tool; do not add a package hook
executor, provider call, synthesis backend, or global dependency resolver.

Before packaging, verify fresh FIFO/skid simulation evidence and run the
composed producer with a deterministic end-to-end scoreboard. Store the wrapper
as a third package whose dependencies bind both leaf content digests. Root
simulation verification is performed by this fixed example runner, not by
pretending its report is one of the existing generic verified-profile schemas.

Remove only the three temporary producer source copies, materialize the lock,
rehash every materialized package file, and compile only the selected three RTL
files under the consumer workspace. Use the same trusted testbench, copied
from the checked-in example; never execute test or hook code found in packages.
The environment has an explicit allowlist and no repository import path.
Retain exact source hashes, argv, harness hashes, log, XML, coverage, and VCD.

## Evidence and limits

The scoreboard exercises ordered delivery, occupancy accounting, capacity,
backpressure, stall stability, simultaneous transfers, reset with queued data,
and drain. Both runs must pass and their deterministic coverage and source
hashes must agree. Unit fixtures validate rejection gates but are not simulation
evidence. New output directories are required; failed attempts are preserved.
Deadline/output limits terminate the task-owned simulation process group.

This validates only the fixed composition and parameters. Source-path isolation
is not an OS sandbox: the original repository still exists, but no producer path
appears in the consumer compile filelist or PYTHONPATH. Package payloads remain
untrusted until deterministic verification; no remote publication is implied.
Immutable published release artifacts are unchanged.
