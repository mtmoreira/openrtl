# ADR 0031: design-agent acceptance evidence tiers

Status: M40 local implementation checkpoint validated; live qualification pending.

## Decision

Keep deterministic orchestration, installed-package checks, existing production
canaries and newly generated circuit acceptance as distinct evidence lanes.
The new read-only `acceptance` command checks the current session, exact optional
expected specification, immutable source blobs, current runtime-bound simulation
artifacts, model-test count, signoff, final acceptance and pending warnings.
Corrupt evidence is an error, never silently downgraded to a passing report.

A consistent local receipt is not independently authenticated provider or
runtime provenance. Even a fully consistent fabricated unit fixture must remain
labelled `live_qualification: not_established_by_this_report`. Real qualification
requires an observed, explicitly authorized evaluation of a frozen candidate,
the reviewed input, bounded provider/runtime settings, and an adequacy review.
Role separation alone is not proof of independently generated verification.

The installed-target smoke verifies the exact package tree and entry point in
fresh CLI processes with an environment allowlist. It tests local chat/review,
save/resume, refusal without provider configuration, batch review stops and
multi-file import. It retains bounded command diagnostics, does not call providers
or run imported/generated code, and explicitly reuses existing interpreter
dependencies. It is not clean-machine installation qualification.

Three public input specifications contain no generator templates, reference
solutions or passing receipts. Further genuinely held-out inputs are selected
after the candidate is frozen. A separate normalized guide/spec archive avoids
changing the manifests used to validate already-published toolkit releases.
The candidate is identified by source/package/artifact digests, not by an
assumption that its unchanged 0.4.0 metadata denotes the published release.

## Consequences

No test can promote scripted orchestration into live circuit acceptance. No
report launches tools, grants approval or mutates the session. Pending warnings
must be acknowledged for local gates to be fully satisfied, including a session
accepted under broad delegation. Requirement/test links remain declarations,
not measured coverage. Hardware PPA, synthesis, formal proof and FPGA execution
remain outside the V1 boundary. A new release identity and publication are
separate from local M40 preparation and validation.
