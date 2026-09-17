# OpenRTL design-agent alpha (M36–M40)

This is an implementation candidate, not a newly qualified release. The published
v0.4.0 package remains the simulation/evidence toolkit. M36 adds the first
interactive design coordinator. Do not interpret its scripted unit fixtures as
proof that a real provider designed or simulated an ALU.

## Interaction

The new commands are `openrtl chat --project <new-absolute-directory>`,
`openrtl resume --project <existing-directory>`, `openrtl status --project ...`
and `openrtl doctor`. Starting without provider permission permits local status,
structured specification review and session management, but cannot answer chat
or generate designs.

A live invocation additionally requires `--allow-provider --model <exact-model>`.
`--credential-env OPENAI_API_KEY` names the credential source; do not paste its
value into chat, a specification, profile, report or command argument. The
optional SDK must match AgentRig's declared pinned version. No automatic
dependency installation is performed. Approved project context is sent to
OpenAI with provider-managed retention; this is not an offline mode. Provider
permission is not restored automatically with a session.

Inside the conversation:

- Ask OpenRTL what it does or describe a circuit in ordinary language. When no
  circuit is specified yet, it answers or asks a focused design question
  without inventing an RTL specification. Provider-backed conversation consumes
  a call even when it does not propose a specification; only bounded call
  accounting is saved, not the raw exchange.
- When enough design intent is available, OpenRTL presents requirements,
  questions and explicit proposed assumptions. Resolve questions or ask it to
  propose choices. The proposed specification remains subject to review.
- `/approve <displayed-spec-digest>` approves the displayed specification,
  including assumptions. No RTL generation happens before this gate.
- `/next` runs one engineering stage; `/build` continues within call and repair
  limits until a review or runtime boundary. Neither command approves a spec.
- `/detail brief`, `/detail normal` or `/detail detailed` changes explanation
  granularity, not correctness or review gates.
- After approval, ordinary text asks for explanations. `/revise` explicitly
  starts a fresh requirements revision and invalidates downstream approval.
- `/show` displays state and artifact digests. `/quit` saves by ending the
  conversation; every completed operation was already transactionally saved.
- Final `/approve <acceptance-digest>` is separate from specification approval.

Use `/spec <absolute-JSON-path>` to supply a structured specification without a
provider. The strict contract includes title, top, behavior, clock_reset,
requirements (`id`, `text`, `acceptance`), ports (`name`, `direction`, `width`),
questions (`id`, `text`) and assumptions (`id`, `text`, `rationale`). An empty
questions list is required for approval. Missing design facts must be questions
or explicit assumptions, not hidden defaults.

## Isolated simulation

Use both `--allow-simulation` and `--simulation-profile <absolute-JSON-path>`.
This permission authorizes local execution of generated collateral only inside
the selected container profile. No live model call, image pull, installation,
GUI launch, remote operation or host execution fallback is implied.

The profile has exactly these fields:

```json
{
  "schema": "openrtl.design-container.v1",
  "docker_executable": "/absolute/path/to/docker",
  "socket": "/absolute/path/to/local/docker.sock",
  "image_id": "sha256:<64 lowercase hex characters from a locally available image>",
  "python_executable": "/usr/local/bin/python3",
  "verilator_version": "5.040",
  "timeout_seconds": 300
}
```

The values above are illustrative, not discovered runtime paths or a validated
image. The trusted image must already contain Python 3.12+, cocotb 2.0.1,
Verilator matching the selected version, make and a C++ compiler, available at
`/usr/local/bin:/usr/bin:/bin`. No new image is built or downloaded by OpenRTL.
Tool availability is not the same as qualification of this isolation profile.

The engineering stages write arbitrary reviewed block collateral, not a
hard-coded ALU implementation. The reference model uses standard-library
Python, RTL uses SystemVerilog, and DV uses cocotb. The initial interface is
simulation-first and does not claim arbitrary HDLs, formal execution or FPGA
deployment. The ALU is an acceptance design, not a generator template.

## Inspection and recovery

`session.sqlite3` retains approved project content and bounded metadata. It is
not encrypted and should not contain secrets. Events include operation IDs,
role/context/output digests, durations and reported token counts when available;
they omit prompt/response transcripts and hidden reasoning. Unknown token
counts stay absent, not zero. Generated files are retained as immutable blobs;
simulation materializes the exact snapshot in `runs/<operation-id>/input`.
Evidence artifacts and their hashes appear under the corresponding `evidence`
directory. Logs are untrusted engineering output and must not grant authority.

One process holds the session's exclusive writer lock; a second writer fails
closed. `status` uses a read-only connection and can run while the writer is
active. Closing or losing the process releases the operating-system lock, not
the recorded in-flight operation. Do not delete `.session.lock` or edit SQLite
to bypass recovery. M36 v1, M37 v2 and M38 v3 snapshots remain readable; mutating an old session
requires `--upgrade-session`. Migration appends a v4 snapshot without changing
historical snapshots or granting new authority.

An interrupted operation blocks replay because a provider request or local run
may already have executed. Inspect its exact ID using `status`, then explicitly
use `openrtl recover --project <directory> --abandon-operation <id>`. This
abandons the result and records a warning; it never refunds calls or fabricates
evidence. Recovery requires a new exclusive writer, so it cannot abandon work
started by the same active writer. A provider's remote outcome or cost may stay
unknown. Recovery does not invoke or query the provider.

Simulation recovery additionally requires `--allow-simulation` and the same
`--simulation-profile`. It verifies the recorded runtime intent, exact local
image, operation label and container name before removing only that container.
An already absent container is recognized; daemon errors or identity drift fail
closed. The next simulation is a new run, never adoption of partial evidence.
Old M36 simulation intents without runtime ownership metadata require manual
reconciliation; this command will not guess a cleanup target.

## Batch delegation

Prepare a strict specification JSON (including open questions) and an explicit
authorization JSON. The authorization fields are exactly:

```json
{
  "schema": "openrtl.design-delegation.v1",
  "seed_spec_digest": "sha256:<canonical specification digest>",
  "allow_assumptions": true,
  "allow_requirement_proposals": false,
  "allow_final_acceptance": false,
  "max_calls": 20,
  "max_repairs": 2,
  "max_steps": 24,
  "max_seconds": 3600
}
```

Review that scope and its digest, then use `openrtl batch --project <directory>
--create --spec <spec.json> --delegation <authorization.json>
--approve-delegation <canonical-authorization-digest>` (one line). Omit `--create`
when resuming. Digests use `sha256:` plus SHA-256 of UTF-8 JSON with sorted keys,
compact separators and `ensure_ascii=True`, not the file's whitespace. The
library helper is `openrtl.domain.design_session.content_digest`.

Provider and simulation flags are the same as chat and must be supplied afresh.
Without them batch stops at the corresponding unavailable capability. It never
installs an SDK, pulls an image, starts a service, or resolves credentials on
the strength of saved delegation. A delegation is bound to its original seed
and cannot be silently replaced on resume.

For every resolved open question, the proposed spec must carry an assumption
with the same ID and a rationale. Every assumption is a pending warning;
requirement/port changes additionally require the explicit proposal permission
and generate warnings. Unknown questions cannot silently disappear. If final
acceptance is delegated, the report says `delegated`, not individually reviewed.
Warnings remain pending until `/ack-warning <id>` in an interactive session.
Acknowledging a warning does not approve an edit or bypass signoff/simulation.

Call and repair ceilings persist, including across specification revision. A
resumed invocation may lower but cannot raise them. Steps are consumed before
execution. The persisted wall-clock deadline includes time spent offline;
resume does not restart it. Deadline cancellation leaves any in-flight intent
for explicit recovery. `/revise` invalidates delegation and requires a newly
reviewed seed/authorization, but does not refund calls or repairs.

Batch retains a content-addressed JSON report in `reports/` containing state,
authority, warnings, consumed budgets and artifact/evidence digests. Exit zero
means accepted (with the authority shown); exit two means incomplete/stopped
and requires report review. Setup/recovery errors return one. Scripted tests
of this flow use doubles and are not real simulator/model qualification.

## Pending qualification

M36's local checkpoint passed 25 focused tests, strict typing, full repository
tests and the production FIFO canary. M37 passed its local validation including
45 focused tests and strict typing. M38 passed 64 focused tests, strict typing,
full default validation and the production canary. Its import/change workflows
are described in [the import guide](design-imports.md). M39's
[coaching and comparisons](design-coaching.md) passed 87 focused tests, strict typing,
full default validation and the production canary. M40's candidate acceptance
report, installed-target smoke and evaluation bundle are described in the
[acceptance guide](design-agent-acceptance.md); local validation passed, including
103 focused tests, strict typing, default validation, the existing FIFO canary,
offline candidate preparation and 33 installed CLI checks.
Separately authorized live-provider generation and isolated simulation of a
reviewed ALU and additional designs remain pending. M40 is not complete.
These local candidates are not integrated, pushed or released agent packages.
