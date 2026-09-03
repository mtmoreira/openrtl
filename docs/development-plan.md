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

## Convergence

M33 adds a fixed FIFO-to-skid-buffer composition as a real dependency-closure
consumer. Passing leaf evidence and a fresh composed producer run precede
packaging. A source-path-isolated consumer recompiles materialized RTL and must
reproduce the producer scoreboard coverage after temporary producer copies are
removed. Preserve both runs' source hashes, coverage, results, and waveforms;
keep this distinct from M32's synthetic dependency-graph tests.

Stop and escalate after three materially equivalent repair failures, two repair
cycles without measurable evidence improvement, or a configured time/cost
ceiling. The escalation records the failure signature, tried hypotheses,
evidence, ambiguity, and requested decision. It never contains hidden reasoning.
