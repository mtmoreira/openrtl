# OpenRTL V1 development plan

## Milestone slices

1. Establish repository, architecture, validation, and licensing.
2. Extend AgentRig with portable bound tools, guarded CLI tools, and MCP server
   configuration.
3. Prove a synchronous-FIFO Verilator/cocotb/VCD canary.
4. Implement artifact, decision, evidence, anchor, and context-pack contracts.
5. Implement expert bindings, build/learn policies, and orchestration.
6. Implement reusable design packages, compatibility, hierarchy, and catalog.
7. Implement plans, reference-model traces, RTL/DV run bundles, and closure.
8. Implement diagnosis, waveform navigation, teaching, reviews, and evaluations.

The initial repository delivers these as coherent local commits on one isolated
product worktree. FPGA, synthesis, formal execution, remote catalog publication,
and automatic waveform-viewer launch remain deferred ports; the local waveform
workbench supports only an explicit user-selected viewer launch. Future
evidence attaches to the same artifact graph and requirement IDs.

## V1 completion gate

- Provider-free unit and integration lanes pass.
- Strict static typing passes when the development environment is available.
- The package imports without simulation or provider extras.
- A synchronous FIFO runs through Verilator/cocotb and emits standardized logs
  plus a VCD trace when the external toolchain is selected explicitly with the
  repository validation flag; the default lane reports that simulation was not
  selected and performs no implicit toolchain execution.
- The selected simulation lane emits a deterministic hash-bound evidence
  manifest. Fail-closed ingestion verifies the retained log, results XML, RTL,
  and waveform before the scripted end-to-end workflow traces the real run
  through package candidacy in build and learn modes.
- No live provider call, remote publication, GUI launch, or remote Git effect is
  part of validation.
- Bounded VCD inspection and deterministic Surfer command files are available
  from the CLI; detached GUI launch requires an explicit executable and flag.
- Evidence-linked FIFO debug sessions explain pre-edge handshakes and post-edge
  state, bind waveform and source anchors, and fail closed on invariant
  violations without editing RTL or launching a GUI.
- Failed debug sessions can be attached to the Diagnosis and Closure Engineer's
  deterministic context and converted into non-applying repair proposals that
  cover every finding with exact requirement, source, and waveform anchors.
- A deterministic FIFO level-update fault case retains its VCD, debug report,
  proposal, and Surfer focus while the passing Verilator canary remains intact.
- An explicitly reviewed repair can be applied only to a separate candidate
  after proposal, session, change, source, and canonical edit-plan digests pass.
  Concrete FIFO instructions reside in a reviewable example artifact rather
  than Python application code. The same deterministic Verilator stimulus
  retains failing and repaired VCDs and proves the linked finding disappears
  without modifying production RTL. Both traces and their causal-signal focus
  must extend beyond the finding edge so the post-edge difference is visibly
  inspectable rather than existing only at the terminal VCD timestamp.

## Convergence

Stop and escalate after three materially equivalent repair failures, two repair
cycles without measurable evidence improvement, or a configured time/cost
ceiling. The escalation records the failure signature, tried hypotheses,
evidence, ambiguity, and requested decision. It never contains hidden reasoning.
