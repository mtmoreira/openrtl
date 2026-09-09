# Import, explain and revise existing collateral (M38 local checkpoint)

For the normal file-selection, in-chat approval and export flow, use the
[M44 import/evolution guide](import-evolution.md). The structured commands below
remain advanced compatibility interfaces.

These commands are development candidates, not the published v0.4.0 toolkit.
They do not call a provider, launch a container or execute imported Python.
M38 local validation passed; real provider/container qualification is separate.
M39 conversational proposal additions are described in [the coaching guide](design-coaching.md).

## Import multiple files

Use a JSON manifest with exactly `schema` and `files`:

```json
{
  "schema": "openrtl.design-import.v1",
  "files": [
    {"source": "my_block.sv", "target": "rtl/my_block.sv", "digest": "sha256:<raw-file-hash>"},
    {"source": "test_block.py", "target": "dv/test_block.py", "digest": "sha256:<raw-file-hash>"},
    {"source": "spec.md", "target": "docs/spec.md", "digest": "sha256:<raw-file-hash>"}
  ]
}
```

The placeholders are not valid digests. File digests are SHA-256 of exact bytes;
the reviewed import-plan digest uses `content_digest` from
`openrtl.domain.design_session` (canonical JSON, sorted keys, compact separators,
ASCII escaping). Review the paths, content and digest before importing:

```sh
openrtl import --project /absolute/new-session --create \
  --source-root /absolute/collateral --import-plan /absolute/imports.json \
  --approve sha256:<reviewed-import-plan-hash>
```

Omit `--create` for an existing idle discovery session. Use `--upgrade-session`
explicitly for v1/v2/v3 sessions in the M39 candidate. Input files are relative to the chosen source
root; targets belong to `rtl/`, `dv/`, `model/` or `docs/`. Allowed suffixes are
SV/Verilog sources/headers, Python for DV/model, and MD/TXT/JSON for documents.
Source and target suffixes must match. Select at most 64 files, 256 KiB each and
512 KiB total. Files must be UTF-8 LF text. No hidden files, credentials, private
keys, links, archives, install hooks or directory sweeps. Never select secret
material, including secret material renamed to an ordinary document.

An exact retry is a no-op. A new batch should list new targets only; it cannot
replace an earlier imported target. Imported bytes survive source changes and
session resume. `/revise` retains imports while clearing current design approval.

In chat, `/import <request.json>` accepts exactly `source_root`, `plan` (the
manifest object), and `approved_digest`. Imports can be explained before any
execution using `/explain <question>` with explicit provider permission. Plain
chat in discovery continues requirements discussion. References
remain untrusted; source instructions cannot grant tools or authority.

## Adopt a runnable baseline

An RTL-only import can be explained or used as reference material. To run an
existing baseline, also import its independent model tests and cocotb suite,
provide a complete reviewed specification using `/spec`, and prepare the strict
simulation manifest documented in the design-agent contracts. Importing a spec
document does not automatically approve its requirements.

`openrtl baseline --project ... --manifest /absolute/run-manifest.json` prints
the complete plan and digest without mutation. Review them, then rerun with
`--approve <printed-digest>`. `/baseline <manifest.json>` also previews the plan
inside chat; say `approve this baseline` to adopt that displayed review. Adoption records inputs
only. Resume with explicit simulation permission and `/next` to run a fresh
isolated simulation. Imported reports or old waveforms cannot substitute for it.

## Review an existing-design change

After a complete baseline exists, prepare a request object with exactly:

- `specification`: the full proposed specification, with no unanswered questions;
- `stage_paths`: all six keys `architecture`, `verification_plan`,
  `reference_model`, `rtl`, `assertions`, `dv`, each mapping to an explicit list
  of writable target paths (empty means retain that stage's collateral);
- `manifest`: the intended complete simulation manifest for the revised design.

`openrtl change --project ... --request /absolute/request.json` prints a plan
bound to the current inputs. `/change-plan <request.json>` previews it in chat.
Review every writable path, retained test/model and changed requirement. In chat,
say `approve this change` for the current displayed plan. For the advanced CLI,
save only the printed `plan` object, then approve it with:

```sh
openrtl change --project /absolute/session --plan /absolute/reviewed-plan.json \
  --approve sha256:<printed-plan-digest>
```

Approval does not run a model or simulator. Resume and use `/next` or `/build`
with explicit permissions. Each stage may write exactly its selected paths;
all other files remain unchanged. The DV manifest cannot drift. Empty stage
scopes do not imply that existing tests adequately cover changed behavior—this
must be reviewed, then evaluated by fresh simulation and signoff. All previous
run/review/acceptance evidence is invalidated for the new candidate.

Use `status` and session event history to inspect import provenance, selected
scope, calls and evidence. No original source file is overwritten. Removing,
renaming or promoting files in an external design repository is not part of
this workflow. Measured optimization and broader DV improvement remain later
milestone work, not a claim made by these import gates.
