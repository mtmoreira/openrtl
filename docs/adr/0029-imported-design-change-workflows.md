# ADR 0029: imported design baselines and scoped revisions

Status: implementation candidate; local validation pending.

## Decision

Explicit import manifests identify multiple source files, target paths and raw
byte digests. Review of the canonical manifest digest authorizes copying those
bytes into immutable session storage, not execution. All paths and metadata are
checked before payload reads. Hidden, sensitive-name, unsupported-extension,
symlink, hard-linked, nonregular, oversized or changed inputs fail closed.
Import batches are atomic; sources remain untouched. Exact retries are
idempotent and conflicting target ownership is rejected.

Session v3 retains separate import metadata and content-addressed blobs. Absolute
source-root paths are not persisted. Imports are marked untrusted references in
versioned expert contexts; RTL text is excluded from independent model and DV
roles. Explanation anchors resolve against imported or current bytes; the current
artifact wins if it has the same path. Importing logs never proves a run passed.

Adopting an existing design requires an exact reviewed spec/import/manifest plan.
The adopted files begin with no simulation, signoff or acceptance. Only the
separately authorized isolated runner can create new run evidence. An imported
model/test suite is existing collateral, not an independently derived model.

An existing-design change plan binds the base input digest, base file hashes,
new specification, exact per-stage writable paths and intended simulation
manifest. Each path belongs to one stage. All other files are retained byte for
byte. Empty stage scopes reuse existing collateral without claiming it was
newly generated. Any change approval invalidates simulation, signoff, acceptance
and prior delegation, but preserves cumulative budgets and historical snapshots.
Candidate repair stays inside the reviewed RTL scope. Fresh simulation and
independent signoff remain mandatory before acceptance.

## Limits

The initial contract accepts bounded UTF-8 LF text, not archives, binary files,
arbitrary install scripts or automatic directory discovery. It does not infer
specification truth from source code or prove imported tests are adequate.
Imports and scoped changes have local structured CLI/interactive planning
commands; conversational change planning and broader DV improvements continue
in the following milestones. Provider/runtime permissions are never inferred
from imported data, and no synthesis or PPA optimization is claimed.
