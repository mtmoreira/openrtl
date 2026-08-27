# ADR 0005: Bind repair proposals to failed debug evidence

Status: accepted

A failed debug session may be attached directly to a Diagnosis and Closure
Engineer context pack as a digest-bound `debug.session` item. The attachment
does not replace project artifacts or hiddenly extend shared memory: its URI,
canonical payload digest, stable session identity, and summary participate in
the deterministic context-pack identity.

OpenRTL can derive a reviewable repair proposal from that session. Every
proposed change must cover named findings and exactly matching requirement
IDs, use source anchors already present in the session, and use waveform
anchors already present on those findings. A proposal that omits a finding,
introduces an unverified anchor, or is built from a passing session fails
closed.

The FIFO adapter maps known invariant categories to bounded RTL repair
strategies. It does not edit RTL. Proposal JSON therefore carries
`applies_changes: false`, a proposed status, the selected expert role, and an
explicit validation sequence. A deterministic level-update fault fixture
proves the path without corrupting production RTL: it retains the synthetic
VCD, debug session, proposal, and a Surfer command file focused on the failing
edge.
