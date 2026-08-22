# OpenRTL repository guidance

## Purpose and boundary

- Build a simulation-first, evidence-driven RTL design assistant on AgentRig.
- Keep provider/runtime/tool lifecycle generic in AgentRig; keep requirements,
  RTL, DV, waveform, coverage, reuse, and EDA semantics in OpenRTL.
- Do not implement deferred FPGA, synthesis, formal execution, device, or
  remote-publication behavior in V1. Preserve their typed ports only when an
  implemented V1 consumer proves the boundary.

## Architecture invariants

- Persist reviewable artifacts, decisions, anchors, and run evidence instead of
  treating provider sessions or hidden reasoning as shared agent memory.
- Every expert invocation receives a versioned role-specific context pack.
- Trace requirements through plans, implementation, tests, assertions, runs,
  diagnoses, and reviews using stable identifiers.
- Treat generated/reused collateral as untrusted until deterministic local
  validation passes. Never execute package-supplied install hooks.
- Keep build and learn modes over the same engineering state machine; teaching
  changes interaction policy, not engineering correctness.
- Tool/model/runtime selection is explicit and must fail closed when a required
  capability is unavailable.

## Security and effects

- Never read or retain credentials, `.env` files, raw private prompts, or model
  reasoning. Emit only bounded, standardized log fields.
- Process adapters use exact argv, fixed roots, environment allowlists,
  deadlines, output bounds, and no shell.
- GUI launch, provider calls, remote catalog synchronization, publication, and
  all remote Git effects require explicit authorization.

## Validation

- Run focused unit tests while developing and `python tools/validate.py` before
  each milestone commit.
- Run the Verilator/cocotb canary separately when the toolchain is available.
- Review the exact staged manifest and diff before every commit.
