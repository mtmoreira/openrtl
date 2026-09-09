# Conversational review and batch policy

Start from the checked-in `./openrtl` launcher and the [first-run guide](first-run.md).
An authorized provider is needed to interpret a new design request and propose
engineering details. Setup, saved preferences and specification approval never
grant provider or simulation permission. Managed runtime setup and live agent
qualification are still pending; see [simulation status](managed-simulation.md).

## Review in conversation

Describe the circuit in ordinary language. For example:

```text
Design a four-bit unsigned ALU with addition and XOR.
Propose the missing overflow and timing decisions as assumptions for review.
review
approve this specification
continue
```

The agent must record interfaces, widths/signedness, clock/reset, timing/latency,
handshake, exceptional behavior and acceptance decisions. Each category cites
existing requirements and relevant ports. Not-applicable choices need an explicit
explanation; unresolved choices block approval. Ports must all be covered by
interface and width/signedness decisions, and acceptance covers every requirement.
Read the decisions and assumptions: a populated checklist does not guarantee
engineering completeness or correctness.

Approval applies only to the exact review displayed in this invocation. If the
state changes, ask for `review` again. No digest copying is required. Ambiguous
agreement such as “yes” requests clarification and does not approve or execute.
Restoring a session displays a new review; it never restores old approval tokens
or execution permissions. The separate final phrase `accept this design` applies
only after simulation and signoff and rechecks retained evidence bytes.

`continue` respects saved pacing. “Go step by step”, “continue automatically”,
“keep it brief” and “explain in detail” change presentation/pacing only. They do
not advance a stage or grant approval. Explicitly say `continue` afterward.

After a complete baseline, requests such as “can you add a reset input?” propose
a change, “improve the tests” proposes DV work, and “explain the reset behavior”
asks for an explanation. Proposals show the previous/proposed specification and
allowed file paths. `approve this change` applies only that shown proposal.
Before the baseline is complete, explicitly revise the specification instead.
Unrecognized or ambiguous intent asks for clarification rather than guessing.
This is bounded routing, not a claim of universal language understanding.

Say `revoke provider permission` or `revoke simulation permission` to disable that
capability for the current invocation. Later conversation text cannot re-enable
it; restart with the established explicit invocation options if needed. Revoking
simulation also disables runtime recovery contact for that invocation.

Use `/help` for advanced commands. Existing structured specifications and digest
commands remain available for compatibility. Older specs are readable without
inventing readiness evidence; ask the authorized design lead to complete the
checklist before using normal conversational approval or the normal batch route.
Readiness is a versioned extension (`openrtl.design-readiness.v1`); previous
stored specs and their approval hashes are not rewritten. Role context is now
`openrtl.design-context.v4`, with v2 discovery response schemas. Change-planning
schemas preserve legacy spec shape when evolving an older baseline.

## Ordinary batch inputs

The normal batch interface creates its internal bounded delegation plan. You
do not write delegation JSON or compute a hash. For an existing reviewed spec:

```sh
./openrtl batch --project ./design --create --spec ./specification.json --policy review
```

For a text request, use `--intent ./request.md` (or `.txt`) instead of `--spec`.
The request is limited to 16,000 bytes, must be an explicitly selected nonhidden
regular file and cannot be a symlink. Discovery requires explicit provider
permission. Without it, batch saves an actionable `provider_permission_required`
report and stops; it does not invent a specification or wait for a setup prompt.
Raw intent text is not saved in the event log. Reviewed requirements may contain
the engineering information extracted from it.

| Policy | Assumptions | Requirement changes | Final acceptance |
| --- | --- | --- | --- |
| `review` | Not delegated | Not delegated | Individual review |
| `assumptions` | Allowed, each warned | Not delegated | Individual review |
| `explore` | Allowed, each warned | Allowed, each warned | Individual review |

The command prints scope and limits. Assumptions and changed requirements receive
individual durable warnings and are printed before the next batch step. Changing
ports, readiness decisions or other engineering requirements requires `explore`;
`assumptions` does not authorize those changes. `--allow-final-acceptance` is a
separate explicit choice; delegated acceptance still needs reverified simulation
evidence. Neither option grants provider or simulation capability.

Use `--max-calls`, `--max-repairs`, `--max-steps` and `--max-seconds` to bound work.
Defaults are 40 calls, 2 repairs, 128 steps and 1800 seconds. Initial text discovery
counts toward calls and time. On resume use the same policy and bounds, omit the
initial intent, and provide execution permissions explicitly again. Existing
delegation cannot be widened or replaced, consumed calls are not refunded, and
the saved deadline is never renewed. Interrupted operations require explicit
reconciliation rather than automatic replay.

Batch returns zero only when accepted; a review, missing permission, budget limit
or other stop returns a nonzero result and retains a report with `next_action`.
The legacy `--delegation`/`--approve-delegation` route remains mutually exclusive
with `--policy`; it cannot be mixed with normal policy options.

## Qualification limits

Local tests use labeled provider/simulator doubles and an offline actual launcher.
They validate routing, review binding, state persistence, denial and recovery
contracts. They do not prove an agent designed an ALU or any other new RTL.
M41b clean-user acceptance, M42b managed runtime, M46 live engineering qualification
and M47 release remain pending. Temporary development handoffs are never part of
customer setup.
