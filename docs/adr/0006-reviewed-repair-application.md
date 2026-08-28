# ADR 0006: Apply repairs only to reviewed candidates

## Status

Accepted, with its FIFO-specific transformation mechanism superseded by ADR
0007. The candidate-only boundary remains current.

## Decision

OpenRTL applies a repair only when the caller supplies the exact proposal ID,
every explicitly approved change ID, and a non-empty review note. The proposal
must remain linked to an unchanged failing debug session and unchanged source
anchors. ADR 0007 moves concrete transformations into typed edit artifacts.

Application writes a separate candidate file; it never edits the reviewed
source or production RTL in place. The application report records before and
after source digests, changed lines, authorization identifiers, and candidate
path. Unsupported changes, stale evidence, stale source, unsafe paths, and
unrecognized existing output all fail closed.

Qualification runs the original fault source and repaired candidate through
the same deterministic cocotb test. It retains both VCDs, results, logs, source,
proposal, debug session, application report, Surfer focus files, comparison,
and a hash-bound evidence manifest. The ordinary production FIFO canary remains
a separate regression gate.

## Consequences

- proposal generation remains non-applying;
- review authorization is explicit and inspectable;
- a repair cannot silently drift from its evidence or source;
- before/after waveforms show whether the linked finding disappeared;
- promotion of a candidate into production remains a later reviewed action.
