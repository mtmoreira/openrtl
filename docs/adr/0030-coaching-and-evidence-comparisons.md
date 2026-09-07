# ADR 0030: reviewable coaching and evidence-bound comparisons

Status: implementation candidate; local validation and live qualification pending.

## Decision

Session v4 adds persistent pacing, a pending change proposal and anchored
analysis. Explicit append-only migration accepts v1/v2/v3 without refunding
budgets, altering existing approvals or restoring provider/runtime permissions.
Historical snapshots remain unchanged. Raw requests and hidden reasoning are
not logged. Structured proposed requirements, scopes, assumptions and findings
are reviewable engineering artifacts rather than conversation transcripts.

Natural-language preference shortcuts change explanation detail or the pacing
used by `/continue`. They do not execute work or authorize calls by themselves.
All pacing options share the existing state machine and stop at review gates.

A tool-free change-planning role returns a complete proposal, never code edits
or approval. The user must approve its exact digest before the M38 change gate
revalidates the baseline. DV-only proposals retain requirements, RTL and model
bytes, allowing only verification-plan and DV writes. Simulation-experiment
proposals retain specification and manifest and allow only the RTL stage to
write. These constraints are checked both when receiving and approving output.
Feature proposals may propose requirements, but still require review.

Diagnosis distinguishes source observations, current simulation observations
and hypotheses. Requirement IDs and source anchors are checked deterministically.
No diagnosis changes files or replaces independent signoff.

New simulation v2 receipts pin the profile and exact trusted runner bytes used.
Comparison reads two historical run snapshots, rechecks their report, intent,
artifact hashes/sizes and runner, then extracts per-test `sim_time_ns` from XML.
Legacy receipts without runtime bindings cannot support this comparison.
Changed requirements, manifests, non-RTL collateral or runtimes make duration
comparison ineligible. Missing durations never fall back to host wall time.

## Evidence limits

The result reports observed simulated duration, declared test changes and file
changes. It never equates fewer nanoseconds or more test names with verified
optimization, functional coverage, equivalence, synthesis or hardware PPA.
RTL assertion sources can differ, and generated test output is not independent
attestation. A candidate still needs fresh simulation, independent adequacy
review and explicit acceptance. Scripted fixtures test these contracts only;
M40 must qualify real provider/runtime and installed-package workflows.
