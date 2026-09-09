# Import, review, evolve and export

Use the checked-in `./openrtl` launcher and [first-run guide](first-run.md).
The following local candidate extends the immutable import contracts; live
provider/design qualification remains pending. Temporary owner-shell development
handoffs are not customer setup.

## Select and review files

In `./openrtl chat --project ./design`, select individual files explicitly:

```text
/select "/path/to/source files" "block.sv" "model.py=model/model.py" "test_model.py=model/test_model.py" "test_block.py=dv/test_block.py" "requirements.md"
approve this import
explain this circuit
```

Quotation marks preserve spaces. Names are relative to the chosen root. Existing
`rtl/`, `model/`, `dv/` and `docs/` subpaths keep their names. Otherwise Verilog
sources/headers map to `rtl/` and MD/TXT/JSON map to `docs/`, using the filename.
Use `source=target` to choose a different role or disambiguate filenames. Python
outside an explicit role directory always requires a target; its role is never
guessed. Duplicate targets are rejected.

The complete preview displays root, source/target names, byte sizes and all
selected content. The manifest and byte hashes are computed automatically.
`approve this import` binds to that exact displayed review and session state.
Changed bytes or a replaced source root require a new preview. Import copies
immutable references into the session; original files are never overwritten.
Importing is not requirement approval, baseline adoption, execution or evidence.

Supported input: UTF-8 LF text in `.sv`, `.svh`, `.v`, `.vh`, `.py`, `.md`, `.txt`
and `.json` files. Limits are 64 files, 256 KiB per file and 512 KiB total. All
paths and metadata are checked before reading any payload. Hidden/sensitive paths,
links, hardlinks, special files, directory sweeps, archives and install hooks are
rejected. Do not select secrets disguised as ordinary source documents. PDF,
DOCX and OCR are not supported.

Explanations use explicitly authorized provider calls and source/line references.
No provider permission is inferred from an import. Anchors must exist; checking
an anchor does not prove the explanation's engineering accuracy. Imported source
instructions cannot grant tool permissions.

## Adopt or complete an imported design

First discuss the intended behavior with the authorized design lead until the
specification has explicit readiness decisions and no unresolved questions. The
[conversation guide](conversational-review.md) explains those decisions. Remain in
discovery while preparing the imported baseline/completion review.

For a complete set of RTL, independent model tests and cocotb DV, say:

```text
prepare imported baseline
approve this baseline
continue
```

The planning expert proposes a complete simulation manifest using existing files,
with exact current requirements and no generation paths. Review the specification,
all files, top, sources, tests, seed and proposed requirement/test links. A link
is a coverage proposal, not proof that the test checks the requirement. Baseline
approval records the adopted files; `continue` needs separate simulation permission
and fresh isolated simulation. Old imported reports never satisfy that gate.
Advanced `/baseline /path/manifest.json` also previews and approves inside chat.

If you have RTL but lack independent model/DV collateral, say:

```text
complete imported design
approve this completion
continue
```

The planner proposes missing documentation, model tests and DV, keeping the exact
current requirements. Review every writable path and test mapping. Completion
cannot overwrite an imported file or write any RTL/assertion-stage file. Model
and DV roles do not receive imported or current RTL text. Their generated outputs
still need deterministic validation, fresh simulation and adequacy review.
The existing versioned stage-scope contract persists this work; resume does not
restore provider/runtime permission. Automatic RTL repair outside that scope is
refused. A separate reviewed change is required to modify the original RTL.

## Review changes

After a complete baseline exists, ask to add a feature, improve the tests or try
an optimization. Review the complete proposed requirements and writable paths,
then say `approve this change`. The advanced `/change-plan /path/request.json`
now follows the same in-chat approval pattern without leaving the conversation.

DV-only proposals retain all RTL/model bytes and requirements. RTL experiments
retain model/DV and the simulation workload; feature changes have their own
reviewed scope. Every applied change invalidates prior simulation and signoff.
Simulation-duration comparisons describe only the measured fixed workload; they
do not establish PPA, synthesis quality, formal equivalence or timing closure.

## Export explicitly

```text
/export "/path/to/new export directory"
export this design
```

The preview lists every source/evidence path and size. Export contains current
RTL/model/DV/docs, immutable originals under `imports/`, reviewed spec/manifest,
and verified retained run evidence when present. `openrtl-export.json` records
exact file hashes/sizes, provenance, revision and evidence limits. The README
explains reimport and execution prerequisites. No session database, raw provider
history or active runtime/recovery authority is copied. Portable session backup
and restoration are separate M45 work.

Existing destinations, including empty directories, are never overwritten.
Destination parents must already exist; links and destinations inside the session
are rejected. Writes use anchored directory descriptors. Interrupted exports keep
an `INCOMPLETE` marker; preserve that directory and choose a new destination to
retry. Missing/tampered or nonpassing run evidence blocks an evidence export.
Use `/export-sources "new directory"` to explicitly exclude run evidence; its
manifest records `explicitly-excluded`, never a fabricated passing result.

The advanced `./openrtl export-design --project ./design --destination ./new-export`
prints a preview and approval digest. Repeat with `--approve <digest>` to export;
`--sources-only` explicitly excludes evidence. It never invokes a provider/runtime.

M44 local tests use labeled fixtures and offline launcher flows. M44's unfamiliar
design explanation/feature/DV acceptance still needs live qualification under
M46. M41b/M42b/M43b and M47 remain pending; none of these local contracts proves
that the real agent can design new RTL.
