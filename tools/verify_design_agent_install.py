"""Provider-free CLI smoke test against an exact installed-target package.

This uses the existing interpreter's dependencies, not a clean-room dependency
installation. No generated RTL or DV is executed. Every output is retained in a
new caller-selected directory; an existing directory is never overwritten.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any


CASES = ("alu8.json", "counter4.json", "arbiter4.json")
GUIDE_FILES = ("docs/design-agent-alpha.md", "docs/design-imports.md", "docs/design-coaching.md",
               "docs/design-agent-acceptance.md", "examples/design_acceptance/README.md",
               *("examples/design_acceptance/" + name for name in CASES),
               "tools/__init__.py", "tools/verify_design_agent_install.py")
BOOTSTRAP = '''import sys
from pathlib import Path
from importlib.metadata import distribution
target=Path(sys.argv.pop(1)).resolve(strict=True)
sys.path.insert(0,str(target))
import openrtl
assert Path(openrtl.__file__).resolve().is_relative_to(target / "openrtl")
dist=distribution("openrtl")
assert Path(dist.locate_file("openrtl")).resolve()==target / "openrtl"
entry=next(e for e in dist.entry_points if e.group=="console_scripts" and e.name=="openrtl")
assert entry.value=="openrtl.cli:main"
sys.argv[0]="openrtl"
code=entry.load()()
for name,module in tuple(sys.modules.items()):
    if name=="openrtl" or name.startswith("openrtl."):
        path=getattr(module,"__file__",None)
        assert path is not None and Path(path).resolve().is_relative_to(target / "openrtl")
raise SystemExit(code)
'''


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError(code)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def safe(path: Path) -> Path:
    selected = path.absolute()
    require(".." not in selected.parts and not any(p.is_symlink() for p in (selected, *selected.parents)), "path_unrecognized")
    return selected


def package_digest(target: Path) -> str:
    root = safe(target / "openrtl")
    require(root.is_dir(), "installed_package_missing")
    paths = sorted(root.rglob("*"))
    # Inspect metadata/name allowlists before reading any selected payload.
    for path in paths:
        safe(path)
        require(not any(p.startswith(".") for p in path.relative_to(root).parts), "hidden_package_payload")
        require(path.is_dir() or path.is_file() and (path.suffix == ".py" or path.name == "py.typed") and
                path.stat().st_nlink == 1 and path.stat().st_size <= 512 * 1024, "package_payload_unrecognized")
    pins = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
    require("cli.py" in pins and "adapters/_design_runner.py" in pins and "adapters/design_acceptance.py" in pins,
            "agent_package_incomplete")
    return digest(pins)


def build_evaluation_archive(root: Path, output: Path) -> Path:
    """Normalized guide/spec companion, not a published or qualified release."""
    root, output = safe(root), safe(output)
    require(not output.exists(), "evaluation_archive_must_be_new")
    selected = [safe(root / name) for name in GUIDE_FILES]
    require(all(p.is_file() and p.stat().st_nlink == 1 and p.stat().st_size <= 512 * 1024 for p in selected),
            "evaluation_source_unavailable")
    payloads = [p.read_bytes() for p in selected]
    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            # Tar assembly in memory avoids the GzipFile/IO typing mismatch.
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in zip(GUIDE_FILES, payloads, strict=True):
                    info = tarfile.TarInfo("openrtl-design-agent-evaluation/" + name)
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    archive.addfile(info, io.BytesIO(data))
            compressed.write(buffer.getvalue())
    return output


def verify_install(target: Path, output: Path, specs: Path, expected_package_digest: str) -> dict[str, Any]:
    target, output, specs = safe(target), safe(output), safe(specs)
    require(package_digest(target) == expected_package_digest, "installed_package_bytes_differ")
    require(not output.exists(), "smoke_output_must_be_new")
    inputs: dict[str, Any] = {}
    for name in CASES:
        path = safe(specs / name)
        require(path.is_file() and path.stat().st_nlink == 1 and path.stat().st_size <= 512 * 1024,
                "specification_input_unavailable")
        inputs[name] = json.loads(path.read_bytes())
    output.mkdir(mode=0o700)
    (output / "ownership.json").write_bytes(canonical({"schema": "openrtl.installed-agent-smoke.v1",
                                                        "package_digest": expected_package_digest}))
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}
    checks: list[dict[str, Any]] = []

    def run(label: str, arguments: list[str], stdin: str = "", expected: int = 0) -> str:
        # Exact argv, no shell, no inherited credential/environment variables.
        result = subprocess.run([sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(target), *arguments],
                                cwd=output, env=env, input=stdin.encode(), capture_output=True, timeout=45)
        require(len(result.stdout) + len(result.stderr) <= 2 * 1024 * 1024, "cli_output_exceeds_bound")
        (output / (label + ".stdout")).write_bytes(result.stdout)
        (output / (label + ".stderr")).write_bytes(result.stderr)
        require(result.returncode == expected, "installed_cli_check_failed_" + label)
        checks.append({"check": label, "returncode": result.returncode,
                       "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                       "stderr_sha256": hashlib.sha256(result.stderr).hexdigest()})
        return result.stdout.decode("utf-8")

    doctor = json.loads(run("doctor", ["doctor"]))
    require(doctor["provider_calls"] is False and doctor["credential_resolution"] is False and
            doctor["simulation_performed"] is False, "doctor_effects_invalid")
    run("help", ["--help"])
    for name, spec in inputs.items():
        case = name.removesuffix(".json")
        spec_path = output / name
        spec_path.write_bytes(canonical(spec))
        project = output / (case + "-session")
        run(case + "-chat", ["chat", "--project", str(project)],
            "/spec " + str(spec_path) + "\nkeep it brief\n/quit\n")
        state = json.loads(run(case + "-status", ["status", "--project", str(project)]))
        require(state["spec"] == spec and state["approved_spec"] is None and state["calls"] == 0 and
                not state["files"] and state["detail"] == "brief", "installed_spec_review_gate_failed")
        revision = state["revision"]
        run(case + "-resume", ["resume", "--project", str(project)], "/show\n/quit\n")
        resumed = json.loads(run(case + "-resumed-status", ["status", "--project", str(project)]))
        require(resumed == state and resumed["revision"] == revision, "resume_changed_engineering_state")
        report = json.loads(run(case + "-acceptance", ["acceptance", "--project", str(project),
                               "--expected-spec", str(spec_path)], expected=2))
        require(report["status"] == "pending" and report["measurement"] is None and
                report["live_qualification"] == "not_established_by_this_report", "smoke_promoted_to_live_evidence")
        run(case + "-approve", ["resume", "--project", str(project)], "/approve " + digest(spec) + "\n/quit\n")
        approved = json.loads(run(case + "-approved-status", ["status", "--project", str(project)]))
        require(approved["status"] == "building" and approved["stage"] == 0 and
                approved["approved_spec"] == digest(spec) and approved["calls"] == 0, "explicit_approval_failed")
        run(case + "-no-provider", ["resume", "--project", str(project)], "/next\n/quit\n")
        stopped = json.loads(run(case + "-stopped-status", ["status", "--project", str(project)]))
        require(stopped == approved, "missing_provider_permission_changed_state")

    # Independent batch command: an unanswered question is a durable review stop,
    # not authorization to call a provider or silently invent a requirement.
    seed = json.loads(canonical(inputs["counter4.json"]))
    seed["questions"] = [{"id": "counter.choice", "text": "Review this acceptance scenario before proceeding."}]
    seed_path = output / "batch-seed.json"
    seed_path.write_bytes(canonical(seed))
    plan = {"schema": "openrtl.design-delegation.v1", "seed_spec_digest": digest(seed),
            "allow_assumptions": False, "allow_requirement_proposals": False, "allow_final_acceptance": False,
            "max_calls": 8, "max_repairs": 0, "max_steps": 8, "max_seconds": 300}
    plan_path = output / "delegation.json"
    plan_path.write_bytes(canonical(plan))
    batch = output / "batch-session"
    result = json.loads(run("batch-review-stop", ["batch", "--project", str(batch), "--create", "--spec", str(seed_path),
                              "--delegation", str(plan_path), "--approve-delegation", digest(plan)], expected=2))
    require(result["outcome"] == "awaiting_review", "batch_review_not_retained")
    batch_state = json.loads(run("batch-status", ["status", "--project", str(batch)]))
    require(batch_state["calls"] == 0 and batch_state["approved_spec"] is None, "batch_permission_bypass")

    source = output / "import-source"
    source.mkdir()
    payloads = {"spec.md": b"# Imported reference; not execution evidence.\n",
                "design.sv": b"// Import-only fixture; never compiled by this smoke test.\n"}
    rows = []
    for name, data in payloads.items():
        (source / name).write_bytes(data)
        rows.append({"source": name, "target": ("docs/" if name.endswith(".md") else "rtl/") + name,
                     "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    import_plan = {"schema": "openrtl.design-import.v1", "files": rows}
    import_path = output / "import-plan.json"
    import_path.write_bytes(canonical(import_plan))
    imported = output / "import-session"
    run("multi-file-import", ["import", "--project", str(imported), "--create", "--source-root", str(source),
                               "--import-plan", str(import_path), "--approve", digest(import_plan)])
    state = json.loads(run("import-status", ["status", "--project", str(imported)]))
    require(len(state["imports"]) == 2 and not state["files"] and state["simulation"] is None,
            "import_misclassified_as_execution")
    require(package_digest(target) == expected_package_digest, "installed_package_changed")
    report = {"schema": "openrtl.installed-agent-smoke.v1", "status": "passed",
              "package_digest": expected_package_digest, "checks": checks,
              "spec_digests": {name: digest(spec) for name, spec in inputs.items()},
              "provider_calls": False, "credential_resolution": False, "generated_simulation": False,
              "dependency_isolation": "existing_interpreter_dependencies",
              "qualification": "installed_target_cli_only_not_live_design_acceptance"}
    (output / "report.json").write_bytes(canonical(report) + b"\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--spec-directory", type=Path, required=True)
    parser.add_argument("--expected-package-digest", required=True)
    args = parser.parse_args()
    try:
        report = verify_install(args.installed_root, args.work_directory, args.spec_directory, args.expected_package_digest)
        print(json.dumps({"status": report["status"], "report": str(args.work_directory / "report.json")}, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError):
        print("Installed design-agent smoke failed; inspect retained bounded local diagnostics.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
