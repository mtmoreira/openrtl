# OpenRTL web interface: implementation handoff

Prepared from local evidence on 2026-09-17 UTC. This is a development handoff,
not release qualification or an assertion of current GitHub state.

## Start here

Read [the web interface plan](web-interface-plan.md) and
[the fresh-session prompt](web-interface-session-prompt.md), followed by
`AGENTS.md`, `docs/architecture.md`, `docs/pending-acceptance.md`,
`docs/design-agent-alpha.md`, and the current personal workflow skills.
Reconcile repository state before changing anything.

The implementation base is branch `codex/agent-local-integration` at
`5412bd3e3ea90d504c64c9c9e03832ee1ad297dc`, before this documentation commit.
The documentation milestone is `codex/web-interface-plan`; its intended local
integration and GitHub publication target is `codex/agent-local-integration`.
Use the verified descendant containing these documents as the starting point
for W1. Do not start from the older toolkit `main` by mistake.

The public repository is <https://github.com/mtmoreira/openrtl>.
At preparation, local `main` and cached `origin/main` both identified
`b0ce3ad9ade81a69081b6306389f0338b43dab70`. These cached refs are not a fresh
remote observation. The owner requested a commit and push of the completed
work; publication is pending until a controller receipt verifies the exact
remote branch commit. Do not infer publication from this file's presence.

## Product request and scope

The designer wants to use natural language to develop their own digital RTL
circuits. The interface must include chat, RTL block hierarchy and source
inspection, corresponding DV navigation/source inspection, agent-triggered
simulations, and embedded waveform viewing. Do not make a fixed FIFO/ALU
example, prewritten specification, or canned repair the product workflow.

The planning defaults are a local, single-user browser workspace and code
inspection with reviewed agent edits first. These were recommended, not
separately confirmed answers to the optional planning questions. Direct browser
editing and hosted collaboration are later extensions unless the owner steers
otherwise. Keep runtime, hierarchy, and waveform implementations replaceable.

Deliver W1 through W5 as sequential validated local milestones. W1/W2 can
proceed while the real owned-guest simulation transport remains unfinished.
Ordinary customers must use a packaged product launcher; development worktrees,
temporary owner-shell scripts, manual digests, and frontend build tools must
not become customer onboarding.

## Completed source checkpoints

The integration reflog records sequential fast-forwards through these anchors.
Before publishing or asserting that all completed source is included, verify
that every anchor is an ancestor of the proposed publication commit.

| Checkpoint | Commit |
| --- | --- |
| M40 design-agent acceptance implementation | `71bc8b81640580a19f623bc98183dafc96fabc88` |
| M41 setup/public dependency attestation | `e6a5c433c1942ac89634c002054ec3cb0b80578e` |
| M42 managed simulation selection | `9a188a627c62f39ee5e6f45c71672a4260ca51f4` |
| M43 conversational review | `876089a1b8402683760afb0c664078541d4f6b7c` |
| M44 imported design evolution | `781589fd7b5a292c27a3a003bc08ca022bc92daf` |
| M45 session diagnostics | `488c976cb63ac69e4bac962a84ec71eb64e4ca23` |
| M42b retirement SDK diagnostic, after managed-backend slices | `ce2a8982abac3f344130a3edafc0801fd7fc17b2` |
| Public AgentRig SDK pin | `c9836d8a374df96f19b7382bbf947669439ea757` |
| Natural-language discovery | `5412bd3e3ea90d504c64c9c9e03832ee1ad297dc` |

Publishing the integration tip preserves the reachable implementation history;
it does not require publishing every retained development branch or promoting
the agent into `main`. Preserve existing branches/worktrees and unrelated edits.

AgentRig `0.3.1.dev12` was already published as a prerelease from source commit
`069711dd53aa3ee64d06afe530b59d4a2d740a8a`. Retained evidence records successful
anonymous verification of its release assets. Its wheel SHA-256 is
`de39b9143a3b4f94daa0b270e5da1a8900521b1b9cc5d02bd9c2afe12f8c257d`.
No new AgentRig publication is needed for this documentation handoff.

## Latest evidence and its limits

Natural-language discovery commit `5412bd3` has parent `c9836d8` and subject
`Support natural-language design discovery`. Its six-file candidate passed
56 focused tests and 481 repository tests in the milestone worktree, plus the
two three-test model suites. The repository validator reported success; the
Verilator/cocotb canary was not selected. The integration report SHA-256 is
`58291c34d53e9dc5ecd987772103acefbc41c946f416e1228edbec2f21d5dd1f`.

The owner subsequently reported that natural-language chat was working. This
is useful owner-observed chat feedback, not a captured fresh-circuit design,
independent DV, simulation, or signoff acceptance result.

The fixed discovery contract returns a natural reply and an optional structured
specification. Plain replies are transient; there is not yet persistent
structured pre-specification conversation continuity. Add explicit requirements,
decisions, assumptions and open-question memory for the web workflow. Do not
silently persist raw private prompts or full chat transcripts.

| Acceptance area | Status at handoff |
| --- | --- |
| M41-M45 local implementation | Integrated; not a blanket live qualification |
| M41b clean-user macOS/Linux onboarding | Explicitly deferred by owner |
| M42b owned-backend lifecycle | Significant retained local/owner validation; complete customer simulation path still pending |
| M42b packaged design workload transport | Pending: host input/control paths need qualified guest staging/access |
| M43b/M44b broader live engineering | Pending; informal chat success does not close these |
| M46 fresh agent-designed RTL qualification | Pending |
| M47 release | Pending; branch publication is not a product release |

Fixtures, mocked providers, deterministic test passes, and the existing FIFO
canary do not prove that the real agent can design a new circuit.

## Reusable implementation map

| Area | Source |
| --- | --- |
| Async agent operations and expert/simulator protocols | `src/openrtl/application/design_agent.py` |
| Review decisions bound to state and payload digests | `src/openrtl/application/design_conversation.py` |
| Immutable snapshots/blobs and exclusive session writer | `src/openrtl/adapters/design_session_store.py` |
| Session domain, artifacts and simulation manifest | `src/openrtl/domain/design_session.py` |
| Bounded progress, accounting and safe diagnostics | `src/openrtl/application/design_diagnostics.py` |
| Exact simulation inputs, runtime invocation and run artifacts | `src/openrtl/adapters/design_simulation.py` |
| Bounded VCD inspection | `src/openrtl/adapters/waveform_workbench.py` |
| VCD signal/transition representation | `src/openrtl/adapters/waveforms.py` |

Build a shared application controller/API, not a terminal scraper or a second
engineering state machine. Coordinate one writer per project; account for CLI
and multiple browser tabs. A browser disconnect must not retry an operation.
Cancellation must distinguish requested, confirmed, and uncertain outcomes.

Actual RTL instance hierarchy is new work. Derive it from elaboration tied to
the selected source and configuration. The current file map is not such a graph.
DV navigation uses many-to-many links between tests, blocks and requirements.

Existing VCD inspection uses a full in-memory parse with a 64 MiB input bound
and a two-million-transition parser bound. Browser viewport APIs/caching and
viewer integration still need implementation. Preserve X/Z and precise time;
integer femtoseconds must not be silently rounded by JavaScript numbers.

## Validation and delivery rules

- Follow applicable `AGENTS.md`, the current `milestone-branch-workflow`, and
  the managed Git wrapper for authorized local Git mutations.
- Inspect actual worktree/index state, preserve unrelated changes, stage only
  the intended manifest, review its diff, and verify each commit after creation.
- Run focused meaningful tests, then `python tools/validate.py` before each
  milestone commit. Use the repository's existing pinned toolchain.
- Keep fixture tests separate from real provider/runtime acceptance. Prepare
  independent work before asking for an owner-environment step.
- Ask before new production dependencies, installs/downloads, provider calls,
  exact credential handling, or additional publication. Existing authorization
  for this handoff's source-branch push does not authorize future releases.
- Never inspect, stop, restart, or reconfigure the separate `agents` account's
  always-on Docker runner. Use only explicitly selected, owned backends.
- Preserve local retained evidence. Do not force-add ignored sessions, raw
  reports, temporary scripts, VM artifacts, waveforms, or private handoffs to Git.

## Local handoff logistics and known execution boundary

The full historical private handoff remains under `build/openrtl-project-handoff.md`.
This public-safe document summarizes the current starting point without copying
raw private context or the entire development transcript.

The three Markdown sources for this documentation milestone are retained under
`build/retained-evidence/web-interface-handoff-source-v1/docs/`, with their exact
hash manifest `docs.sha256` one directory above. The complete owner controller
is retained alongside them as `openrtl-web-handoff-publish-v1.zsh`.

In the preparation session, the sandbox denied the working-style file, both
workflow skills, project Python execution, bundled Git execution, and access to
the managed Git wrapper. Escalated attempts did not make the required tools
available. No workaround through another account or tool surface was used.
The Markdown files can be prepared there; actual validation, commit,
integration and remote verification require an owner-capable environment.

The controller defaults to local-only work. Its ordinary publication mode uses
the managed wrapper. Historical evidence says that wrapper rejects pushes;
there must be no automatic direct-Git fallback. An explicitly approved exception
may authorize exactly one normal push to `mtmoreira/openrtl` branch
`codex/agent-local-integration`, without force, tags, `main`, or a release.
An uncertain push must be reconciled by reading the exact remote ref.

Only a successful publication receipt with matching local and remote commit
identities closes the current commit-and-push request. If absent, report the
remaining boundary accurately and preserve the prepared work.
