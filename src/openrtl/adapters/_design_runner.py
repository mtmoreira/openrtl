"""Trusted container entrypoint. Never execute this against host-generated collateral."""

from __future__ import annotations

import base64
import contextlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from typing import Any


IMAGE_PYTHON_PATH = "/opt/openrtl/python"
RUNNER_PATH = "/control/run.py"
ISOLATED_BOOTSTRAP = (
    "import runpy,sys\n"
    "dependency,script,mode=sys.argv[1:]\n"
    "sys.path.insert(0,dependency)\n"
    "sys.argv=[script,mode]\n"
    "runpy.run_path(script,run_name='__main__')\n"
)


def isolated_python(mode: str) -> list[str]:
    if mode not in ("--model", "--simulation"):
        raise ValueError("runner_mode_invalid")
    return [sys.executable, "-I", "-c", ISOLATED_BOOTSTRAP,
            IMAGE_PYTHON_PATH, RUNNER_PATH, mode]


def worker(request: dict[str, Any]) -> None:
    from cocotb_tools.runner import get_runner
    root = Path("/output/project")
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "dv"))
    manifest = request["manifest"]
    assert isinstance(manifest, dict)
    assert version("cocotb") == "2.0.1"
    verilator = shutil.which("verilator")
    make = shutil.which("make")
    assert verilator is not None and make is not None
    observed = subprocess.run([verilator, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout
    assert observed.split()[1] == request["verilator_version"]
    Path("/output/toolchain.json").write_text(json.dumps({"cocotb": version("cocotb"), "verilator": observed.strip()}))
    runner = get_runner("verilator")
    build = Path("/output/sim_build")
    runner.build(sources=[root / p for p in manifest["sources"]], includes=[root / "rtl"],
                 hdl_toplevel=manifest["top"], build_dir=build, build_args=["--assert"],
                 always=True, waves=True, timescale=("1ns", "1ps"))
    runner.test(test_module=manifest["test_modules"], hdl_toplevel=manifest["top"],
                seed=manifest["seed"], waves=True, build_dir=build, test_dir=Path("/output"),
                results_xml="/output/results.xml", timescale=("1ns", "1ps"),
                test_args=["--trace-file", "/output/waves.vcd"],
                extra_env={"PYTHONPATH": str(root / "dv") + ":" + str(root)})


def model_tests() -> None:
    root = Path("/output/project")
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / "model"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path("/output/model-results.json").write_text(json.dumps({"tests": result.testsRun,
        "passed": result.wasSuccessful() and result.testsRun > 0 and not result.skipped}))
    if not result.wasSuccessful() or result.testsRun == 0 or result.skipped:
        raise RuntimeError("model_tests_failed")


def main() -> int:
    # Refuse accidental host execution even when this module is imported by validation.
    if not Path("/control/request.json").is_file() or not Path("/input").is_dir():
        raise RuntimeError("isolated_control_mount_required")
    request = json.loads(Path("/control/request.json").read_bytes())
    if len(sys.argv) == 2:
        if sys.argv[1] == "--model":
            model_tests()
        elif sys.argv[1] == "--simulation":
            worker(request)
        else:
            raise RuntimeError("runner_mode_invalid")
        return 0
    shutil.copytree("/input", "/output/project")
    # Generated processes receive no provider credentials or host environment.
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
                   "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"}
    passed = True
    with Path("/output/runner.log").open("wb") as log:
        try:
            for mode in ("--model", "--simulation"):
                subprocess.run(isolated_python(mode),
                               cwd="/output", env=environment, stdout=log, stderr=log,
                               check=True, timeout=int(request["timeout_seconds"]) // 2)
        except (subprocess.SubprocessError, OSError):
            passed = False
    artifacts = {}
    model_count = 0
    for name in ("results.xml", "model-results.json", "waves.vcd", "runner.log", "toolchain.json"):
        path = Path("/output") / name
        if path.is_symlink() or not path.is_file():
            passed = False
            continue
        if path.stat().st_size > 16 * 1024 * 1024:
            passed = False
            continue
        data = path.read_bytes()
        artifacts[name] = base64.b64encode(data).decode("ascii")
        if name == "model-results.json":
            with contextlib.suppress(ValueError, KeyError, TypeError):
                model_count = int(json.loads(data)["tests"])
    payload = {"status": "passed" if passed else "failed", "model_tests": model_count,
               "artifacts": artifacts, "error_code": None if passed else "isolated_tests_or_build_failed"}
    encoded = json.dumps(payload)
    if len(encoded) > 48 * 1024 * 1024:
        raise RuntimeError("runner_output_limit")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
