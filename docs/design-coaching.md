# Coaching, reviewed improvements and comparisons (M39 candidate)

This is unreleased development work. Local validation and live model/container
qualification are pending. Published v0.4.0 remains the toolkit and examples.

## Choose detail and pacing

In an existing chat, use `/detail brief|normal|detailed` or say `keep it brief`,
`explain in detail`, or `normal detail`. Use `/pace stage|continuous`, or say
`go step by step`, `one step at a time`, or `continue automatically`.
The phrases are explicit local shortcuts, not unrestricted natural-language
intent classification. Other messages retain the normal chat behavior.

Preferences are saved without executing a step or invoking a provider.
`/continue` then runs one stage or continues until the next review/stop gate.
`/next` always means one engineering step; `/build` explicitly continues through
eligible stages. None approves requirements, accepts a finished design, grants
provider/runtime access, changes budgets or reveals hidden model reasoning.
Fine-grained explanations describe engineering decisions; execution units remain
the validated stages, not individual arbitrary commands.

## Ask for an improvement

With a complete generated or reviewed imported baseline, ask:

```text
/propose-change Add a parity output and propose the acceptance checks.
/propose-dv Improve corner-case and backpressure tests without changing the circuit.
/propose-optimization Propose an RTL experiment while retaining the requirements and workload.
```

These require the existing explicit provider permission. They create a saved,
unapplied proposal containing a summary, complete specification, per-stage write
scope and manifest. Inspect every proposed requirement and assumption, retained
test/model and writable path. `/show` displays the complete proposal and its
review digest. Only `/approve-change <exact-proposal-digest>` adopts the plan.
New inputs make an old proposal stale; it is never applied automatically.

DV-only proposals cannot change the specification, model or RTL, including RTL
assertions. They may change `docs/` verification-plan files and `dv/` files.
An RTL assertion change requires a separately reviewed feature/change scope.
Optimization proposals cannot change the specification or simulation manifest,
model or DV. Their name expresses experimental intent, not a proven benefit.
After approval use `/next`, `/continue` or `/build` with the required explicit
permissions. Prior simulation/signoff/acceptance is invalidated for the candidate.
No external source repository or published design is overwritten.

`/diagnose <question>` produces saved findings linked to requirements and source
lines, with basis `source`, `simulation` or `hypothesis`, and a recommended check.
Simulation-based findings need current run evidence. Diagnosis is advisory; it
does not repair anything. `/explain <question>` remains available for teaching,
including imported reference files before adoption. Neither is formal proof.

## Inspect a before/after experiment

Record a baseline revision after a passing isolated run. After a reviewed change
and another passing run, use `/compare <baseline-revision>` or the read-only CLI:

```sh
openrtl compare --project /absolute/session --baseline-revision 42
```

The revision number is an example, not a universal checkpoint. Comparison reads
the session's immutable snapshot and task-owned run artifacts, verifies their
hashes/sizes and runtime bindings, and reports per-test simulated nanoseconds.
It makes no provider or container call and does not save or apply a decision.
Original run artifacts must remain present. Tampered or missing files fail
closed. Older simulation-v1 receipts lack the runtime bindings and need fresh
runs; migrating the session cannot manufacture those measurements.

Different requirements, manifests, non-RTL artifacts or runtimes yield
`not_comparable` and no duration deltas. Eligible results are labelled
`observations_comparable`, never “optimization proven.” A negative delta means
less recorded simulated time for that test—not lower hardware latency, better
PPA, faster host execution, equivalence or exhaustive correctness. RTL assertion
sources may have changed. Added test names are declared tests, not measured
coverage. Independent signoff must review adequacy and possible weakened checks.

## Resume and evidence boundaries

Use `resume --upgrade-session` explicitly to migrate v1/v2/v3 to session v4.
Pacing, pending proposals and analyses survive resume; old snapshots and budgets
remain intact. Neither migration nor a saved proposal restores permission to
contact a provider or execute generated collateral. The unit suite uses labelled
scripted providers, simulators and measurement files. Only separately qualified
live runs can demonstrate that the real agent designed and simulated a circuit.
