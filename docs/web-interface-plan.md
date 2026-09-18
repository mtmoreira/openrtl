# OpenRTL web interface implementation plan

Status: planned; no web implementation or web acceptance is claimed by this document.

## Product goal

Provide a web workspace in which a designer can develop digital RTL through natural language, inspect the design and verification collateral, run simulations, and debug results using source and waveforms. The browser must expose the same engineering state and evidence as the OpenRTL CLI.

The user's requested core features are:

- Natural-language conversation with the design agent.
- Hierarchical navigation of developed RTL blocks, with source inspection on selection.
- Navigation and source inspection for design-verification (DV) collateral.
- Agent-triggered simulations with visible progress and results.
- Waveform inspection inside the web interface.

The interface must support varied digital RTL designs within the selected toolchain's capabilities. It must explain unsupported constructs or unavailable capabilities rather than imply that every circuit or verification methodology is supported.

## Recommended initial scope

These are recommended defaults, not answers already confirmed by the user:

- Local, single-designer operation in a desktop browser.
- Source inspection and reviewed agent changes before direct browser editing.
- A packaged launcher, provisionally `openrtl ui`, serving packaged frontend assets.
- Existing CLI workflows remain available against the same application layer.
- Completed-run VCD viewing first, including traces from failed simulations.

Do not make customers run frontend build tools or development handoff scripts. A source-development workflow may use build tooling, but the normal installed-product workflow must use packaged assets and documented setup.

Hosted collaboration, accounts, cloud project storage, direct editing, live waveform streaming, synthesis, FPGA deployment, formal execution, and release publication are outside the initial web scope. They require separately agreed scope and relevant authorization.

## Current foundation and boundaries

Existing OpenRTL capabilities provide the starting point:

- The application design agent, natural-language discovery, and structured specifications.
- Persisted design sessions, artifact revisions, engineering decisions, and evidence.
- Change proposals, review decisions, and revalidation concepts.
- Simulation run records and runtime adapter boundaries.
- Bounded local VCD inspection and waveform evidence anchors.

Relevant code and architecture references are `src/openrtl/application/design_agent.py`, `src/openrtl/adapters/waveform_workbench.py`, `src/openrtl/adapters/waveforms.py`, `docs/architecture.md`, and `docs/pending-acceptance.md`. Verify their current contracts before implementation; this plan is not a substitute for inspecting the active integration revision.

The inspected implementation has no general web frontend/API or general elaborated RTL instance hierarchy. The current VCD implementation uses bounded in-memory parsing and is useful for small traces; it is not evidence of scalable browser performance.

The owner has reported that natural-language chat works after the discovery fix. This is informal interaction evidence, not fresh-design qualification. The packaged owned-guest simulation path still needs qualified project-input transport: host-side input/control paths cannot simply be assumed visible to the guest container daemon. Complete that work for real web-triggered simulations while preserving a backend-neutral contract.

Keep existing acceptance status explicit:

- M41b clean-user/Linux acceptance was deferred by the user.
- Remaining M42b owned-backend acceptance, including workload transport, stays pending until verified.
- M43b/M44b live engineering acceptance retains its documented evidence requirements.
- M46 fresh-design live qualification remains pending until actually performed and assessed.
- M47 release remains pending until its checks and publication authorization are satisfied.
- Fixtures, mocked providers, canned collateral, and the existing FIFO canary cannot prove that the real agent can design new RTL.

## Workspace layout and interaction

Use resizable, collapsible areas:

| Area | Content |
| --- | --- |
| Header | Project, active revision, runtime readiness, active operation, and budget state. |
| Left navigator | RTL hierarchy, DV collateral, requirements, and run history. |
| Main document area | Source, proposed diff, specification, requirements, and selected reports. |
| Chat | Natural-language interaction, clarification, progress, review cards, and contextual explanations. |
| Bottom workbench | Problems, simulation logs/results, waveform viewer, and run details. |

Selecting a block, source range, test, requirement, failure, or waveform interval should create an explicit context attachment for chat. The attachment identifies the relevant artifact revision or run, rather than silently depending on the currently visible panel. Agent responses should link back to those stable selections when supported by evidence.

Navigation must remain useful when code is incomplete, the compiler fails, a runtime is unavailable, or there is no simulation yet. Show the reason a derived view is unavailable while retaining access to source and diagnostics.

## Shared engineering model

Expose a transport-neutral application service consumed by both CLI and web. Avoid terminal scraping, web-specific copies of the design state machine, or direct browser access to provider/runtime internals.

The service should provide typed operations for project lifecycle, conversation, requirements and decisions, artifact reading, proposals and review, hierarchy inspection, simulation submission, run inspection, and waveform queries. The browser renders structured state and bounded events; it does not infer successful engineering transitions from prose.

Stable identity and provenance are mandatory:

- Project and session identity.
- Artifact identity, revision, content digest, and contained source path.
- Requirement and decision identifiers.
- Hierarchy identity tied to exact sources, compilation options, top, and parameters.
- Run identity tied to exact RTL, DV, tool/runtime configuration, seed, and evidence.
- Waveform identity tied to its run and trace digest, with explicit time units.

An edit must make affected prior results visibly stale. Preserve those historical results for inspection; do not silently reassign them to the latest revision. Distinguish generated collateral, deterministic validation, compilation, passing tests, and requirement coverage.

## Conversation continuity and change control

Natural-language follow-ups need structured continuity even before a complete specification exists. Persist approved or clearly attributed requirements, assumptions, decisions, open questions, and references to artifacts. Preserve role-specific context-pack boundaries.

The current repository policy excludes raw private prompts and transcripts from persistence. Do not silently save chat text to project storage, logs, browser local storage, analytics, or error reports. Initially, display the current conversation ephemerally and reconstruct a resumable engineering timeline from permitted structured state. Persisted transcripts require an explicit retention-policy decision and authorization before implementation.

Proposed agent changes should expose affected files, a diff, rationale tied to requirements, and required validation. Provide accept/reject actions and preserve review history. Direct browser editing, if later selected, must use the same revision, conflict, and revalidation mechanisms.

## RTL and DV navigation

Provide three distinguishable views where available:

1. The project/file tree, which remains available before elaboration.
2. Intended block structure from structured engineering artifacts, clearly labeled as planned.
3. Actual elaborated instance hierarchy from a qualified compiler/parser adapter.

Actual hierarchy must handle repeated module instances, parameters, generate constructs, source locations, and explicit top selection. Do not present agent assertions or regex extraction as authoritative elaboration. Tie the index to the source/configuration digest and mark outdated indices after edits.

Use an adapter for hierarchy extraction. Evaluate the selected Verilator version's supported machine-readable output before choosing it; current online documentation does not establish compatibility with the repository's pinned toolchain. Adding a parser dependency requires approval.

DV navigation represents available test suites, tests, reference models, assertions, and supporting components such as drivers, monitors, and scoreboards. Do not assume all projects use those components or a particular verification methodology. Link DV artifacts to requirements and RTL blocks through many-to-many relationships. A test can exercise several blocks, and several tests can verify one requirement.

## Jobs, reconnection, and recovery

Long-running provider and simulation operations need durable job identity and explicit states. Define queued, active, completed, failed, cancellation-requested, cancelled, and unknown/reconciliation-needed outcomes; add domain stages such as compilation and execution without conflating them with final success.

- Coordinate one project writer and detect stale client revisions.
- Use operation identity/idempotency to prevent duplicate provider calls or simulations after retries or double clicks.
- Allow a browser to reconnect using a bounded event cursor plus an authoritative state snapshot.
- A page refresh or disconnected client must not silently terminate or duplicate a job.
- Cancellation must reconcile the actual provider/runtime operation. Closing a request or displaying a stopped spinner is insufficient.
- Enforce deadlines and output bounds and distinguish unsupported cancellation from confirmed cancellation.
- Preserve failed-run diagnostics and actionable recovery state.
- Detect multiple tabs attempting conflicting mutations without losing accepted work.

Choose the event transport after comparing the existing application contracts and required cancellation/reconnection behavior. An event stream must not expose hidden reasoning, credentials, raw private prompts, or unrestricted process output.

## Simulation and waveform integration

Both chat requests and explicit UI controls must submit the same typed simulation operation. Show the selected backend, top, test selection, parameters, seed, deadlines, relevant limits, and exact revision before execution. Use existing authorization and budget boundaries rather than creating a second approval system.

Keep runtime lifecycle and generic execution in AgentRig. Keep OpenRTL requirements, RTL/DV staging semantics, results, and waveform interpretation in OpenRTL. Preserve replaceable local backend implementations and never assume a specific owner's machine paths or always-on runner.

First waveform scope:

- Load recorded VCD from completed or failed runs.
- Browse and search signals; display scalar/vector values including unknown and high-impedance states.
- Select radix, pan/zoom, and use two cursors with a time difference.
- Save bounded signal selections and link failures to trace intervals where evidence exists.
- Attach signal/run/time selections to chat.
- Display missing traces, truncation, unsupported formats, and size limits explicitly.

Use contained artifact access, viewport/range queries, bounded responses, and caching/indexing as needed. Measure memory and interaction latency with representative trace sizes; do not transfer or parse unbounded traces in the browser. Treat additional formats, especially FST, as follow-up scope unless needed for accepted size targets.

Keep a viewer adapter so the UI is not coupled to one waveform library. A mature viewer may handle rendering while OpenRTL owns run identity, source links, and engineering evidence.

## Provisional technology choices

The Python application engine should remain authoritative. The frontend framework, HTTP/event server, hierarchy implementation, editor, and viewer require an implementation-time dependency review and explicit approval for any new production dependency.

- [Monaco](https://microsoft.github.io/monaco-editor/) is a candidate for source viewing and diffs. Syntax highlighting is not a substitute for an HDL language server or compiler diagnostics.
- [Surfer](https://docs.surfer-project.org/book/) is a candidate for browser waveform rendering. Verify browser/WASM embedding, artifact loading, navigation/control APIs, licensing, supported versions, and packaging with a small investigation before committing to it. Desktop integration does not prove browser embedding works.
- Existing VCD inspection is a candidate data source for bounded traces, subject to measured limits.
- A Verilator hierarchy adapter is a candidate only after checking compatibility with the selected toolchain.

No technology mentioned here authorizes installation, downloads, provider calls, runtime startup, or publication.

## Local service and packaging

Default to loopback binding and intentional browser access. Apply origin/request protections, contained artifact routes, and project-scoped authorization. Do not expose arbitrary filesystem reads or shell execution. The later owner-requested [local provider controls](provider-controls.md) permit ephemeral key entry in the loopback browser while keeping values out of saved engineering state and responses; runtime authority remains server-side.

Generated RTL and DV remain untrusted until the applicable deterministic validation passes, and execute only through the selected isolated runtime. Preserve existing process allowlists, exact argument construction, fixed roots, deadlines, and output limits.

Package static frontend assets with the product and document a normal installation/launch path. Development scripts may support contributors but must not become customer prerequisites. Show runtime readiness and actionable setup diagnostics through the product's normal interfaces.

## Sequential implementation milestones

W1–W5 are provisional web-workstream labels, not replacements for M41–M47. Execute approved implementation through sequential milestone branches/worktrees with the managed Git wrapper, coherent local commits, validation, and local integration. Preserve unrelated changes and retained evidence. Pushes and other remote changes require their own applicable authorization.

### W1 — Web foundation and natural conversation

Deliver the local service boundary, packaged-launcher skeleton, project create/reopen, chat, structured pre-specification continuity, progress, review cards, and reconnectable operations.

Acceptance:

- CLI and web use the same application operation and revision contracts.
- A project can be created, reopened, and resumed through the browser.
- Follow-up answers update structured requirements/open questions without unintended transcript retention.
- Refresh/reconnect retrieves accurate operation state without duplicate calls.
- Concurrent or stale mutations are rejected or reconciled explicitly.
- Provider-free contract/UI tests are labeled as such; any live conversation check is separately authorized and recorded.

### W2 — RTL/DV workbench and review

Deliver source/file navigation, requirements and decisions, proposals/diffs, history, elaborated hierarchy, and links between DV, blocks, and requirements.

Acceptance:

- Selecting an instance or DV artifact opens the correct source revision.
- Planned and elaborated views are visibly distinct, and compiler failures retain usable source navigation.
- Representative parameterized/generated/repeated instances are handled by the selected hierarchy adapter.
- Many-to-many verification links do not fabricate coverage or imply unsupported DV structure.
- Accepted changes create a new revision and invalidate affected derived views/results.
- Context attachments carry explicit revision/source identities.

### W3 — Real simulation workspace

Complete qualified owned-backend workload transport and expose simulation configuration, submission, progress, logs, results, cancellation, and recovery.

Acceptance:

- Project inputs reach the selected guest/runtime through the backend-neutral staging contract.
- UI and chat start the same simulation operation with the same authority limits.
- A real simulation runs against the exact selected RTL/DV revision.
- Compilation errors, test failures, timeout, cancellation, and lost-connection recovery have truthful states and retained evidence.
- Changing source does not relabel a historical pass as current.
- Existing fixtures/canaries demonstrate infrastructure only; fresh-design claims remain reserved for the relevant live qualification.

### W4 — Waveforms and contextual debugging

Integrate a qualified browser viewer with run/source links, failure navigation, and chat attachments.

Acceptance:

- Supported real traces load from both passing and failing runs.
- Signal values, time units, radix, cursors, and selected intervals agree with bounded deterministic reference inspection.
- A failure can navigate to its supported source/waveform evidence without invented anchors.
- A waveform question carries the exact run, trace, signals, and interval.
- Representative trace sizes satisfy documented latency/memory targets; over-limit behavior is explicit and bounded.

### W5 — End-to-end acceptance and customer packaging

Validate the integrated designer workflow and produce the normal packaged launch/setup documentation.

Acceptance:

- An explicitly authorized fresh design proceeds from natural conversation through requirements, generated RTL/DV, source/hierarchy inspection, simulation, waveform debugging, reviewed repair where needed, and rerun.
- Evidence identifies actual provider calls, tool/runtime versions, exact artifact revisions, test adequacy, results, and limitations.
- Browser refresh, project reopen, and export preserve the permitted engineering state and provenance.
- A customer can launch packaged assets without development handoff scripts or frontend compilation.
- Live acceptance is assessed against the existing milestone criteria. Completing web features alone does not complete M46 or authorize M47 release.

## Validation and completion reporting

Use focused application/API/UI tests for each slice and the repository-required validation before milestone commits. Offline fixtures are appropriate for deterministic error/recovery contracts; label their limits. Add real runtime and provider checks only within explicit authorization, recording actual outcomes rather than inferring success from UI state.

At each completed milestone, report the integrated commit, validations performed, remaining unverified behavior, dependency decisions, and retained worktree/branch state. Keep pending acceptance documents accurate. Final delivery must distinguish implementation completion, local deterministic validation, real runtime qualification, real agent design qualification, and publication.

## Decisions to resolve without blocking independent work

- Use the recommended local/single-user and source-inspection-first defaults unless the owner revises them; ask only when a material decision cannot be resolved from the agreed scope.
- Approve concrete production dependency choices after the compatibility investigation.
- Decide whether persisted raw transcripts are ever desired; current policy prohibits silently introducing them.
- Select representative project/trace sizes and measurable interaction targets before waveform performance acceptance.
- Define the exact live provider/runtime scope and budget before the end-to-end qualification.
