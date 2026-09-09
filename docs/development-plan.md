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
and automatic waveform-viewer launch remain deferred ports; the local waveform
workbench supports only an explicit user-selected viewer launch. Future
evidence attaches to the same artifact graph and requirement IDs.

## V1 completion gate

M35 packages the completed post-0.3 examples into a local OpenRTL 0.4.0 release
candidate. Its installed-wheel acceptance includes FIFO, skid buffer, portable
package closure, and the three-case composed matrix. Release publication follows
qualification; new feature work waits for user feedback on that release.

- Provider-free unit and integration lanes pass.
- Strict static typing passes when the development environment is available.
- The package imports without simulation or provider extras.
- A synchronous FIFO runs through Verilator/cocotb and emits standardized logs
  plus a VCD trace when the external toolchain is selected explicitly with the
  repository validation flag; the default lane reports that simulation was not
  selected and performs no implicit toolchain execution.
- The selected simulation lane emits a deterministic hash-bound evidence
  manifest. Fail-closed ingestion verifies the retained log, results XML, RTL,
  and waveform before the scripted end-to-end workflow traces the real run
  through package candidacy in build and learn modes.
- No live provider call, remote publication, GUI launch, or remote Git effect is
  part of validation.
- Bounded VCD inspection and deterministic Surfer command files are available
  from the CLI; detached GUI launch requires an explicit executable and flag.
- Evidence-linked FIFO debug sessions explain pre-edge handshakes and post-edge
  state, bind waveform and source anchors, and fail closed on invariant
  violations without editing RTL or launching a GUI.
- Failed debug sessions can be attached to the Diagnosis and Closure Engineer's
  deterministic context and converted into non-applying repair proposals that
  cover every finding with exact requirement, source, and waveform anchors.
- A deterministic FIFO level-update fault case retains its VCD, debug report,
  proposal, and Surfer focus while the passing Verilator canary remains intact.
- An explicitly reviewed repair can be applied only to a separate candidate
  after proposal, session, change, source, and canonical edit-plan digests pass.
  Concrete FIFO instructions reside in a reviewable example artifact rather
  than Python application code. The same deterministic Verilator stimulus
  retains failing and repaired VCDs and proves the linked finding disappears
  without modifying production RTL. Both traces and their causal-signal focus
  must extend beyond the finding edge so the post-edge difference is visibly
  inspectable rather than existing only at the terminal VCD timestamp.
- External exact-replacement specifications can be qualified into typed,
  digest-bound edit plans only after proposal, failed-session, source-anchor,
  change, and byte-range validation. The resulting planning report remains
  `awaiting_review`; it does not authorize or apply an edit.
- A provider-neutral Diagnosis and Closure Engineer request binds an exact
  context pack, proposal, failed session, source digest, and ordered changes.
  Strict expert output becomes only an untrusted `awaiting_qualification`
  specification; deterministic qualification and explicit human review remain
  mandatory downstream gates, and validation performs no provider call.
- The request can be executed through one bounded, tool-free AgentRig
  structured-generation turn with exact runtime, capability, provider, model,
  retention, timeout, input/output byte, and output-token selection. The
  provider-free scripted lane retains a canonical envelope and safe lifecycle
  report; capability drift, model drift, tool exposure, stale evidence,
  truncation, extra fields, and oversized output fail closed. Successful output
  remains `awaiting_qualification` and cannot apply RTL.
- A real OpenAI Responses composition is available only through a canonical
  non-executing provider plan followed by the provider-specific command,
  `--with-openai-provider`, and the exact plan digest. The plan fixes the SDK,
  runtime, capability, model, retention, credential-environment name, and
  single-call bound. Credential resolution is late, validation stays synthetic
  and network-free, and successful output remains `awaiting_qualification`.
- Provider-produced edit specifications reach review only through an exact
  provenance chain binding the provider plan, one-call execution receipt,
  invocation report, suggestion report, edit-spec bytes, proposal, failed
  session, source, and deterministic edit plan. Qualification is provider-free,
  emits a non-applying `awaiting_review` receipt, and rejects cross-run mixing.
- Applying a provider-qualified plan requires a separate human approval bound
  to the exact qualification and edit-plan digests, proposal, ordered changes,
  and review note. The adapter revalidates the canonical qualification,
  planning report, edit plan, failed session, and source before writing only a
  separate candidate and emitting a qualification-bound application receipt.
  Production RTL remains unchanged and renewed simulation plus visibly distinct
  before/after waveforms remain mandatory evidence.
- Candidate promotion review starts only after those receipts and renewed
  artifacts are rehashed into a canonical non-applying plan. The plan binds the
  exact candidate and current target digests and remains
  `awaiting_promotion_approval`; production replacement is a later explicit
  human-signoff gate.
- Qualified promotion requires an independent exact-plan signoff, atomically
  replaces only the named target with the approved candidate bytes, and emits a
  digest-bound receipt. The broken regression fixture remains separately named.
- The 0.2.0 release candidate binds a library-only wheel, source distribution,
  and deterministic examples archive to one exact commit. A clean environment
  must install the wheel with AgentRig 0.2.2, extract the examples archive, and
  pass the model, fault-diagnosis, and Verilator repair walkthroughs without
  importing OpenRTL from the repository checkout. Tagging and publication are
  separate owner-authorized operations.
- Published-release acceptance starts only from the public GitHub release and
  exact public AgentRig 0.2.2 source commit. It verifies immutable asset bytes,
  installs into an isolated environment without inherited repository imports,
  safely extracts the companion archive, and repeats the model, diagnosis, and
  Verilator repair examples. The retained acceptance report contains only
  public identities, hashes, versions, and pass/fail state.
- Post-release development advances to OpenRTL 0.3.0 on the exact published
  AgentRig 0.3.0 contract. The locked sibling checkout, package metadata,
  public imports, provider-free suite, synthetic provider lifecycle, and
  Verilator/cocotb lane must all agree on that version. The immutable OpenRTL
  0.2.0 public-acceptance lane remains pinned to AgentRig 0.2.2 and unchanged.
- OpenRTL 0.3.0 release-candidate qualification builds deterministic wheel,
  source, and examples artifacts from one exact commit, installs the dependency
  only from public AgentRig `v0.3.0`, and reruns the installed model, diagnosis,
  visibly distinct waveform repair, and Verilator lanes. The resulting local
  qualification is evidence for review, not authorization to tag or publish.
- Published OpenRTL 0.3.0 acceptance is a separate public-input lane. It binds
  annotated OpenRTL `v0.3.0` and AgentRig `v0.3.0` to their exact commits,
  verifies the immutable release manifest and asset bytes, installs both
  packages in isolation, and reruns the installed examples with Verilator and
  the visible waveform distinction. The historical 0.2.0 lane remains
  unchanged and runs alongside it in CI.
- Post-0.3 development includes a one-entry ready/valid skid buffer as the
  second complete RTL example. Its reference model, synthesizable RTL,
  randomized cocotb test, and deterministic same-edge refill fault pass through
  the shared debug-session and evidence schemas while protocol interpretation
  remains in block-specific adapters. The fault trace must deassert `s_ready`
  and lose occupancy at the refill edge; the production trace must accept the
  replacement and remain occupied. Both traces, the non-applying proposal, and
  the Surfer focus are retained without modifying the correct production RTL or
  any immutable 0.3.0 release artifact.
- FIFO and skid-buffer passing simulations can be ingested through checked-in
  block-neutral verified-simulation profiles and normalized into the same
  `VerifiedRunEvidence` and `DesignPackage` contracts. Exact schema, source,
  requirement, result, and waveform linkage is mandatory; cross-design mixing
  fails closed. Both candidates can enter one local catalog without remote
  publication, while the published FIFO compatibility command remains intact.
- Simulation-verified candidates can be snapshotted into self-contained local
  bundles whose complete manifest is externally digest-bound. A consumer can
  reload the typed package after the original build tree is gone, rehash every
  source and evidence payload, run deterministic interface/parameter
  compatibility, and atomically materialize source collateral without
  executing package content. Missing, modified, symlinked, incompatible, or
  wrong-manifest states fail closed.
- Portable packages with dependencies resolve through an exact manifest-digest
  pin set into a canonical dependency-first lock. Missing, unused, duplicate,
  cyclic, version-drifted, or digest-drifted selections fail closed. Locked
  consumption reverifies every bundle and atomically materializes an isolated,
  source-only package workspace without executing package content.

## Approved next delivery: M36–M40

M36 builds the first interactive chat-to-design slice: digest-reviewed
requirements and assumptions, persisted engineering state, role-scoped expert
generation, independently derived model/DV collateral, isolated simulation,
bounded candidate repair, signoff and explicit final acceptance. The ALU is a
live acceptance design, never hard-coded generator output. The implementation
checkpoint `a0b47bc615af0989abbbd814f423c8b5d262123b` passed 25 focused
tests, strict typing, full default validation and the production canary. This
does not qualify generated designs: live provider/container acceptance remains
pending, and the checkpoint has not been integrated into main or published.

M37 adds batch operation, scoped broad delegation with a warning for each
assumption, durable review queues, bounded execution and crash reconciliation.
Its checkpoint `9abd92216d11d78f82b7505737015c61ece2e46a` passed 45 focused
tests, strict typing, the 199-test main suite, both three-test example suites
and the production canary. The
batch runner shares M36's gates; delegation never restores provider or runtime
permissions. M36–M40 remain a sequential local delivery, not a released agent.
M38 adds multiple file imports and existing-design/DV baselines, explanations
and controlled change impact. Checkpoint `2f95c5fefdc051b0b92a2a0d515aa80340098cb3`
passed 64 focused tests, strict typing, the 218-test main suite, both three-test
example suites and the production FIFO canary. It adds source-only imports,
frozen provenance, reviewed baseline adoption,
per-stage change paths, retained-file guards and fresh-evidence requirements.
M39 expands teaching granularity, diagnosis, DV-only improvement and measured
simulation-level experiments. Checkpoint `661024ff30e973b9b7dc51aeb329a1d4a33ce819`
passed 87 focused tests, strict typing across 11 files, the 241-test main suite,
both three-test example suites and the FIFO canary. It adds saved pacing, conversational but unapplied review proposals,
requirement/source-anchored diagnosis, and comparison of reverified run artifacts.
Comparisons do not establish equivalence, functional coverage or hardware PPA.
M40 targets held-out designs, installed CLI workflows, documented evidence tiers
and the user-facing package. Its candidate adds reverified read-only acceptance
inventories, three solution-free evaluation specifications, an installed-target
CLI smoke test and a normalized guide/spec archive. Checkpoint
`71bc8b81640580a19f623bc98183dafc96fabc88` passed 103 focused tests, selected
strict typing, the 257-test main suite and both three-test example suites, the
existing FIFO canary, offline candidate preparation and 33 installed CLI checks.
M41 preflight re-ran the default suite and rehashed retained evidence successfully.
The installed smoke deliberately reuses existing interpreter dependencies; it is
not a clean-machine or live-provider qualification. Published evaluation inputs
are not secret held-out designs. Real provider/container execution and additional
held-out trials require separate runtime/model choices and authorization.
No milestone implies synthesis, formal execution or FPGA deployment.

Complete as much local development and deterministic validation as possible
before owner-shell handoffs. Provider calls, runtime installation, publication
and remote Git effects remain separately authorized. A milestone is not
complete merely because a scripted test or state transition succeeded.

## Approved local continuation: M41–M45

M41 adds the checked-in launcher, private resumable first-run configuration,
explicit dependency consent and separate readiness reporting. Its current
validated base uses existing Python 3.12+ and a pinned pure AgentRig
wheel without an editable sibling, pip or package hooks. The base slice passed
50 focused tests, strict typing, 307 repository tests and both three-test model
suites. The actual offline launcher and existing FIFO canary also passed, with
retained evidence rehashed against unchanged production source. The subsequent
implementation adds consent-gated uv 0.12.3 / CPython 3.13.15 provisioning and
hash-locked optional SDK 2.47.0 setup. Its local gate passed 66 focused tests,
strict typing across eight files, 323 repository tests, both model suites and six
actual offline launcher checks. [The runtime/SDK evidence manifest](../evidence/milestones/m41-runtime-sdk-local.json)
binds those results to the implementation commit. A separately approved real
macOS setup run passed private Python provisioning with no Python in PATH,
offline reuse, locked SDK installation and offline SDK readiness. Public AgentRig
download failed. A diagnostic retry passed 69 focused tests, 326 repository tests,
both model suites, strict typing and six offline launcher checks, while both
interpreters received HTTP 404/410 for the pinned public URL. The existing local
release artifact does not establish anonymous public availability.
[The retained evidence](../evidence/milestones/m41-public-dependency.json) records
these outcomes separately. The later approved AgentRig publication passed
anonymous verification of all three immutable assets and actual public-launcher
specification review in fresh private state, using product-provisioned Python.
[Publication evidence](../evidence/milestones/m41-publication.json) records the
source and artifact identities. Split at the environment boundary: M41a local
installation/onboarding implementation is validated; M41b clean-OS-user and Linux
acceptance remains pending. The original complete M41 gate remains open. Do not
repeat installation merely to combine already passing independent lanes, and do
not call owner-isolated state a clean OS user. See [the first-run guide](first-run.md) and
[the local evidence manifest](../evidence/milestones/m41-first-run-base.json).

M42 will manage an explicitly owned isolated simulation runtime and pinned
images, preserving unrelated Docker runners and forbidding host fallback.
Its current local slice adds version-2 identity/resource-bound profiles, private
selection, an explicit fixed infrastructure self-test, rehashed receipts and
owned-operation recovery. Unit process doubles do not qualify a real runtime.
The macOS backend/dependency decision, immutable image distribution and original
real isolated simulator acceptance remain pending; see [the candidate guide](managed-simulation.md).
M43 will complete conversational routing, readiness reviews, shown-review
approvals and ordinary batch policy inputs. M44 will complete import previews,
in-chat evolution, independent collateral for RTL-only inputs and usable source
export. M45 will add session discovery/portable export, bounded progress,
actionable diagnostics and recovery qualification. Continue in sequential
milestone worktrees with validated local checkpoints and retained evidence.

M46 live engineering qualification requires separately authorized actual provider
calls and isolated simulations of new designs plus an independent adequacy review.
M47 release requires clean-user qualification, a distinct immutable identity and
separately authorized publication. Neither is complete or implied by local tests.

## Existing composition convergence evidence

M33 adds a fixed FIFO-to-skid-buffer composition as a real dependency-closure
consumer. Passing leaf evidence and a fresh composed producer run precede
packaging. A source-path-isolated consumer recompiles materialized RTL and must
reproduce the producer scoreboard coverage after temporary producer copies are
removed. Preserve both runs' source hashes, coverage, results, and waveforms;
keep this distinct from M32's synthetic dependency-graph tests.

M34 broadens that proof through a bounded parameter and seed matrix. The
reviewed cases vary width, power-of-two and non-power-of-two FIFO depth, and
random seed. Each case repeats the complete producer/package/materialized-
consumer path and reaches its configured FIFO-plus-skid capacity. A dedicated
CI lane preserves this as regression evidence; it is not a parameter sweep or
formal proof.

Stop and escalate after three materially equivalent repair failures, two repair
cycles without measurable evidence improvement, or a configured time/cost
ceiling. The escalation records the failure signature, tried hypotheses,
evidence, ambiguity, and requested decision. It never contains hidden reasoning.
