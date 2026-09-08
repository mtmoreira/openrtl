"""Offline real-launcher smoke with an explicit retained wheel; no live qualification.

Copies only current application source and the verified wheel into an absent
test directory. Does not install Python, execute package hooks or contact a
provider, network service or simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tools.bootstrap_openrtl import load_pin, read_regular, verify_wheel  # noqa: E402


def _safe(path: Path) -> Path:
    selected = path.absolute()
    if ".." in selected.parts or any(p.is_symlink() for p in (*selected.parents, selected)):
        raise ValueError("verification_path_rejected")
    return selected


def _describe(path: Path, base: Path) -> dict[str, object]:
    data = read_regular(path, 32 * 1024 * 1024)
    return {"path": path.relative_to(base).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data)}


def verify(wheel: Path, output: Path) -> dict[str, object]:
    pin = load_pin()
    data = read_regular(wheel, pin.size_bytes)
    verify_wheel(data, pin)
    output = _safe(output)
    if output.exists():
        raise ValueError("verification_output_must_be_absent")
    output.mkdir(mode=0o700)
    copied = output / "clone with spaces"
    copied.mkdir(mode=0o700)
    sources = ["openrtl", "tools/bootstrap_openrtl.py", "tools/bootstrap_sdk.py",
               "tools/bootstrap_runtime.sh", "bootstrap/dependencies.json", "bootstrap/sdk-requirements.lock"]
    sources.extend(p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "src/openrtl").rglob("*"))
                   if p.suffix == ".py" or p.name == "py.typed")
    source_manifest = []
    for name in sources:
        original = _safe(ROOT / name)
        if any(part.startswith(".") for part in Path(name).parts) or not original.is_file():
            raise ValueError("verification_source_rejected")
        target = copied / name
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        payload = read_regular(original, 2 * 1024 * 1024)
        with target.open("xb") as stream:
            stream.write(payload)
        target.chmod(0o700 if name == "openrtl" else 0o600)
        source_manifest.append(_describe(target, copied))
    (output / "source-manifest.json").write_text(json.dumps(source_manifest, indent=2, sort_keys=True) + "\n")
    wheels = output / "wheelhouse with spaces"
    wheels.mkdir(mode=0o700)
    (wheels / pin.filename).write_bytes(data)
    state = output / "private state with spaces"
    project = output / "design with spaces"
    spec_path = output / "specification.json"
    spec = json.loads((ROOT / "examples/design_acceptance/counter4.json").read_bytes())
    spec_path.write_text(json.dumps(spec) + "\n")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                   "OPENRTL_PYTHON": sys.executable}
    # Deliberately no PYTHONPATH, user site, sibling checkout, SDK or credentials.
    command = [str(copied / "openrtl"), "--state-dir", str(state)]
    checks: list[str] = []
    logs: list[dict[str, object]] = []
    report: dict[str, object] = {
        "schema": "openrtl.onboarding-smoke.v1", "status": "running", "checks": checks,
        "logs": logs, "dependency": {"sha256": pin.sha256, "size_bytes": pin.size_bytes},
        "source_manifest": _describe(output / "source-manifest.json", output),
        "evidence_lane": "offline-existing-python-real-launcher",
        "runtime_installation": False, "downloads": False, "provider_calls": False,
        "credential_resolution": False, "simulation": False,
        "dependency_cache": "verified_retained_wheel_in_temporary_test_state",
        "fresh_machine_qualification": "pending", "m46": "pending", "m47": "pending",
    }

    def save() -> None:
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    def run(label: str, arguments: list[str], *, text: str = "", expected: int = 0) -> str:
        completed = subprocess.run(command + arguments, cwd=copied, env=environment,
                                   input=text.encode(), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=30, check=False)
        if len(completed.stdout) > 2 * 1024 * 1024:
            raise ValueError("launcher_output_limit")
        filename = output / (label + ".log")
        filename.write_bytes(completed.stdout)
        logs.append(_describe(filename, output))
        save()
        if completed.returncode != expected:
            raise ValueError("launcher_check_failed:" + label)
        checks.append(label)
        return completed.stdout.decode("utf-8")

    save()
    try:
        run("unattended_denial", ["--offline", "chat", "--project", str(project)], expected=2)
        if state.exists() or project.exists():
            raise ValueError("denial_created_state")
        run("offline_local_specification_review", ["--allow-install", "--offline", "--wheelhouse", str(wheels),
            "chat", "--project", str(project)], text="/spec " + str(spec_path) + "\n/show\n/quit\n")
        doctor = json.loads(run("cached_doctor", ["--offline", "doctor", "--json", "--require-local"]))
        if doctor["local_review_ready"] is not True or doctor["packages"]["agentrig"] != "0.3.0":
            raise ValueError("pinned_zip_metadata_unavailable")
        if doctor["provider_authorized"] is not False or doctor["simulation_ready"] is not False:
            raise ValueError("unexpected_runtime_authority")
        run("cached_resume_without_install_consent", ["--offline", "resume", "--project", str(project)],
            text="/show\n/quit\n")
        saved = json.loads(run("saved_specification", ["--offline", "status", "--project", str(project)]))
        if saved["spec"] != spec or saved["calls"] != 0 or saved["simulation"] is not None:
            raise ValueError("local_review_state_mismatch")
        cache = state / "dependencies" / pin.sha256 / pin.filename
        if stat.S_IMODE(cache.stat().st_mode) != 0o600:
            raise ValueError("cache_privacy_invalid")
        verify_wheel(cache.read_bytes(), pin)
        checks.append("cached_wheel_rehashed_and_private")
        report["status"] = "passed"
        return report
    except BaseException:
        report["status"] = "failed_or_interrupted"
        raise
    finally:
        save()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = verify(arguments.wheel, arguments.output)
        print("CHECKPOINT offline_real_launcher passed " + str(len(report["checks"])))  # type: ignore[arg-type]
        print("OPENRTL_ONBOARDING_SMOKE_STATUS=0")
        return 0
    except Exception:
        print("OPENRTL_ONBOARDING_SMOKE_STATUS=1; inspect the retained report and bounded logs")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
