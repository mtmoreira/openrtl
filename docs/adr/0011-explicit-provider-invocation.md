# ADR 0011: Require a digest-approved plan for live expert invocation

## Status

Accepted.

## Decision

OpenRTL exposes OpenAI Responses as an explicit composition of M18's bounded
structured-generation contract. Planning and execution are separate commands.
Planning reads only the reviewed expert request and writes a canonical plan
containing the exact adapter version, runtime binding, capability, provider,
model, retention policy, resource bounds, credential-environment name, and
single-call authority. It does not read the credential or create a client.

Execution requires the provider-specific command, `--with-openai-provider`,
and the exact plan digest. Every request, plan, evidence, source, capability,
tool, retention, and model check occurs before AgentRig creates its short-lived
client. Credential resolution is deferred to that creation point. The value is
never logged or persisted. The provider returns the strict M17 schema through
M18's one-turn, tool-free generator contract.

The resulting response remains untrusted and `awaiting_qualification`.
Provider invocation cannot qualify, review, approve, apply, promote, or modify
RTL. Repository validation uses an injected local client and synthetic
credential mapping; it performs no network access or real credential lookup.

## Consequences

- a plan is reviewable without granting network or credential authority;
- a stale request, altered plan, wrong digest, capability drift, tool exposure,
  or source drift fails before credential resolution or provider invocation;
- execution evidence records only safe identities, digests, aggregate usage,
  and the digest of the bounded review note;
- the selected Python environment must separately provide AgentRig's pinned
  OpenAI SDK extra before a real call; no implicit package installation occurs;
- each real call and any later qualification or application remain distinct
  authorization boundaries.
