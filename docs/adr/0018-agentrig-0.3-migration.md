# ADR 0018: Bind development to AgentRig 0.3.0

## Status

Accepted.

## Context

OpenRTL 0.2.0 is an immutable published release whose wheel requires AgentRig
0.2.2. AgentRig 0.3.0 is now separately published and retains the public
command, MCP, structured-generation, and OpenAI Responses contracts consumed
by OpenRTL while adding provider-neutral image execution. Mutating the
dependency metadata of the existing OpenRTL version would make the source tree
claim a different contract than the released 0.2.0 artifacts.

## Decision

- Advance development package metadata to OpenRTL 0.3.0 and require
  `agentrig==0.3.0`.
- Keep the editable sibling source binding, but require validation and CI to
  materialize exact published AgentRig commit
  `31b2ecae0605f0d6b63b5f060c929ca567ae16f2`.
- Add a focused compatibility contract for installed versions, public exports,
  process/MCP bindings, and the Responses client lifecycle.
- Preserve `validate_public_release.py` and the OpenRTL 0.2.0 release contract
  on AgentRig 0.2.2. Historical public acceptance must not silently follow the
  development dependency.
- Treat AgentRig's image runtime as available infrastructure only. OpenRTL
  gains no image workflow, provider call, credential access, or production
  authority from this migration.

## Consequences

Local development needs the exact AgentRig 0.3.0 source at the lockfile's
sibling path. CI checks out that commit explicitly before using `uv.lock`.
Provider-free and synthetic-provider validation must pass before integration,
and the Verilator/cocotb lane remains the hardware-behavior gate. OpenRTL 0.3.0
packaging and publication remain later, separately authorized work.
