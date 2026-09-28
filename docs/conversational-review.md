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
New provider proposals also use the fixed, versioned
[`openrtl.hardware-specification.v1`](hardware-specification.md) document structure.
It records parameters plus purpose/scope, interface, clock/reset/CDC, functional,
timing/performance, exceptional-behavior, and integration sections. The complete
saved document—not a brief generated summary—is shown for review.
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
`openrtl.design-context.v9`, with v11 OpenAI and v6 Ollama discovery response schemas.
Provider-proposed feature changes use the new document format; DV-only and
optimization proposals retain their exact specification, including a legacy
shape. An older baseline remains unchanged unless a complete feature proposal is
explicitly reviewed and approved.

## Discovery continuity and bounded correction

The Design Lead plans at most three material questions in one numbered round.
The displayed questions come from validated structured entries, not a second
unlinked list in model prose. Saved decisions and unresolved questions survive
omission from a later response. An explicit question-to-decision link closes an
open question; omission alone does not. Pending questions remain visible in the
draft without being asked again. Routine choices should be documented as
reviewable assumptions; after three rounds new questions are limited to concrete
interface, clock/reset, behavioral or acceptance blockers. This does not imply
that deterministic checks can recognize every semantic paraphrase.

A locally invalid discovery proposal can receive at most two model correction
attempts within the original request deadline. Every attempt consumes the normal
call budget and, where applicable, a separate provider spend reservation. Each
has its own operation ID and ordinary safe validation code, correlated to the
same workspace request. Only a fully validated candidate updates the saved draft
and decisions. Widths, readiness anchors and answers are never guessed by the
decoder. The rejected candidate is transient correction context; full text is
retained only when explicit private capture is enabled. Ordinary events remain
content-free. Transport failures, cancellation, authority/memory violations and
unknown validation failures are not automatically retried. Restart never replays
a correction. Persistent invalid output still fails closed and leaves the last
validated state unchanged.

The provider's discovery `reply` is a fixed transport sentinel, not user-facing
prose. OpenRTL renders the conversation from validated questions and specification
state. Correction context retains rejected engineering structure but replaces
that non-authoritative reply, preventing verbose prose from consuming the repair
request. Resolving every saved question without proposing a specification is a
correctable validation failure rather than another empty discovery turn.

These are offline-tested lifecycle guarantees, not live model qualification.
M46 engineering qualification and M47 release remain pending.

The [import/evolution guide](import-evolution.md) covers explicit multiple file
selection, source-anchored explanations in discovery, baseline/completion reviews
and source/evidence export inside this conversation.

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

M43a is locally validated at implementation commit
`9e47877af97c66261e1e3c91da8409b7c991b465`. The owner gate passed 381 repository
tests, both three-test model suites, 158 focused regressions, strict typing in
eleven files and all eight offline validation lanes. The evidence manifest is
[`m43-conversational-review.json`](../evidence/milestones/m43-conversational-review.json).
M43b retains live conversational design acceptance with M46; M43 as a whole
remains incomplete. Managed full-suite filesystem failures are retained separately
and are not reported as passing.

Local tests use labeled provider/simulator doubles and an offline actual launcher.
They validate routing, review binding, state persistence, denial and recovery
contracts. They do not prove an agent designed an ALU or any other new RTL.
M41b clean-user acceptance, M42b managed runtime, M46 live engineering qualification
and M47 release remain pending. Temporary development handoffs are never part of
customer setup.
