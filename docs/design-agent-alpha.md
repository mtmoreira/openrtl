# OpenRTL design-agent alpha (M36)

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

- Describe the circuit. OpenRTL presents requirements, questions and explicit
  proposed assumptions. Resolve questions or ask it to propose choices.
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

Concurrent changes fail closed. An interrupted active operation is retained and
blocks automatic replay, because the previous provider request or local run may
already have executed. M37 will add explicit reconciliation. Until then inspect
the recorded state; do not edit the database or claim completion from a partial
run. Model, simulation, review or artifact failures never become acceptance.

## Pending qualification

M36 still requires passing focused tests, strict typing, full repository tests,
the production canary, then separately authorized live-provider generation and
isolated simulation of a reviewed ALU. M37–M40 remain pending. No M36 code has
been committed, integrated, pushed or released by this document.
