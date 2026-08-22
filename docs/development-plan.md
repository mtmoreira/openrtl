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
and automatic waveform-viewer launch remain deferred ports; their future
evidence attaches to the same artifact graph and requirement IDs.

## V1 completion gate

- Provider-free unit and integration lanes pass.
- Strict static typing passes when the development environment is available.
- The package imports without simulation or provider extras.
- A synchronous FIFO runs through Verilator/cocotb and emits standardized logs
  plus a VCD trace when the external toolchain is selected.
- The scripted end-to-end workflow traces requirements through package
  candidacy and demonstrates build and learn modes.
- No live provider call, remote publication, GUI launch, or remote Git effect is
  part of validation.

## Convergence

Stop and escalate after three materially equivalent repair failures, two repair
cycles without measurable evidence improvement, or a configured time/cost
ceiling. The escalation records the failure signature, tried hypotheses,
evidence, ambiguity, and requested decision. It never contains hidden reasoning.
