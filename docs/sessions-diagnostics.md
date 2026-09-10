# Sessions, progress and diagnostics

Use the checked-in `./openrtl` launcher. The local M45 contracts passed owner
validation; live provider/design acceptance remains a separate M46 gate.
Temporary owner-shell validation scripts are development collateral, not setup.

## Find and inspect sessions

```sh
./openrtl --state-dir ./private-state sessions list
./openrtl sessions list --root ./my-projects --limit 20 --offset 0
./openrtl sessions inspect --project "./my-projects/first design" --limit 50
./openrtl resume --project "./my-projects/first design"
```

Listing reads only the selected directory's immediate entries. It defaults to
the selected product state's `projects/` directory. Names, schema, revision,
status, counts and recovery state help select a session. `next_offset` paginates
directory entries, including filtered entries; a page can be empty with another
page available. Limits: 1,000 entries total, 100 per page. Missing directories
produce an error without creating state. Linked/special/hardlinked storage is refused.

Inspection is read-only and can run alongside a writer. It reports operation IDs,
stage, budgets, pending-warning count, bounded events and engineering-state
digests. Events correlate specifications/requirements and artifact sets by digest,
plus role, operation and run IDs when available. Output omits narrative text,
provider/model labels and arbitrary errors. Bounds: 10,000 events, 16 MiB total,
16 KiB per event; the latest 1–200 events are returned. Accounting is not silently
truncated when these limits are exceeded.

Token totals include only received counts. `known_total: null` means no count was
reported; `unknown_calls` remains explicit. A reported zero is distinct from an
unknown count. Persisted call/repair/step/deadline budgets survive resume. No cost
is inferred from absent pricing or usage. Failed/unfinished calls can have unknown
external cost. Missing configuration never authorizes installation or execution.

## Observe progress and recover

Chat and batch display bounded lifecycle progress during pending provider or
simulation work, including stage and elapsed milliseconds. A returned result
still requires deterministic validation. Existing stage/review flows show
engineering summaries after validation. No raw prompt transcript or hidden
reasoning is logged. Display failure cannot authorize work or cause a retry.
Waiting notices stop after 120 updates per operation.

Interruptions retain charged calls and uncertain operation intent. Resume never
replays that intent or restores invocation-local permissions. Inspect the exact
operation first. Existing `recover --project ... --abandon-operation ...` requires
a fresh writer; simulation recovery additionally requires explicit selection and
authorization of the original owned runtime. Recovery adds an uncertainty warning
and does not refund calls. Never guess or adopt a different daemon/container.
Old schemas require explicit `--upgrade-session`, which appends a migration.

## Portable session backup and restore

```sh
./openrtl sessions export --project ./design --destination ./new-backup
./openrtl sessions restore --source ./new-backup --destination ./restored-design
```

Both commands default to a read-only preview. Review the manifest and data notice,
then repeat the command with `--approve <printed-digest>`. Existing destinations
are never overwritten, even empty ones; their parent must exist. Destinations
must be outside the source project/backup. These are explicit source-containing
actions. Source and support exports are separate formats.

`openrtl-session.json` binds canonical history, digest-addressed source blobs and
retained run evidence. It includes specifications, approved engineering history,
summaries, immutable imports, warnings, budgets and required run files. It excludes
provider sessions, raw prompt histories, credentials, private runtime configuration
and transient approval tokens. Self-consistent hashes do not prove authorship or
engineering quality; review the origin of an untrusted backup before restoring it.

Restore validates all records and evidence before writing. It constructs a known
SQLite schema from validated records; it never opens an imported database or
executes exported Python. History/revisions and budgets stay intact, with one
appended restore-provenance event. Engineering approvals are historical records,
not provider/runtime permission. Copied run intents are evidence only; active
operations and cleanup authority cannot transfer. Resume needs fresh explicit
execution authorization as usual.

Limits: 512 snapshots, 4,096 files, 96 MiB overall, 16 MiB per file, 16 MiB snapshot
payload total, 2 MiB per snapshot. Every referenced historical blob must exist.
Run portability requires runtime-bound v2 receipts, matching intent/runner bytes
and all five hashed artifacts. Passing run XML is rechecked; failed runs are never
relabeled passing. Missing, changed or legacy evidence blocks backup. Diagnostics
and M44's explicit sources-only export remain available when full backup cannot
be made. Reconcile unfinished operations in the original project before backup.

Interrupted output retains `INCOMPLETE`; session commands reject that directory.
Preserve it and retry into a new destination. No cleanup, overwrite, migration,
provider call, runtime contact or upload occurs automatically. Restore adds one
history entry, so the history bound also applies to later backups.

## Preview a support bundle

```sh
./openrtl sessions support --project ./design --destination ./new-support
```

Review displayed diagnostics and repeat with `--approve <printed-digest>`.
The bundle contains bounded standardized metadata and hashes, excluding source,
full specifications, narrative summaries, provider/model labels, raw exceptions
and personal project paths. Nothing is uploaded. Known errors give a specific
next action; unknown errors use a fixed generic category. Selected directory names
appear in local session listing but are not included in support bundles.

Local tests use synthetic providers, run receipts and projects. They qualify
storage/recovery/diagnostic contracts only. M41b clean-user/Linux, M42b real managed
runtime, M43b/M44b live design acceptance, M46 and M47 remain pending.

Implementation `b1ae2278e1b838e6c5548e937e8469e2939c9be0` is bound to the
[M45 evidence manifest](../evidence/milestones/m45-sessions-diagnostics.json).
Owner validation passed 435 repository tests, both three-test model suites,
212 focused tests, twelve-file strict typing and nine controller lanes. The
actual offline launcher ran eleven session commands; original history, events
and blobs matched the restored copies, with only the explicit provenance append.
All 92 launcher source hashes were reverified. One final trailing blank line was
removed with identical Python AST; passing local focused/typing rechecks and the
separate managed filesystem failures are retained in the evidence manifest.
