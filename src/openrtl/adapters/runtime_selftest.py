"""Fixed infrastructure self-test, deliberately separate from agent-generated RTL."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openrtl.adapters.design_session_store import safe_root
from openrtl.adapters.waveforms import VcdIndex
from openrtl.domain.design_session import JsonObject, content_digest, require


def collateral(profile: JsonObject) -> tuple[dict[str, str], JsonObject]:
    limits = profile["resources"]
    files = {
        "rtl/openrtl_runtime_selftest.sv": (
            "module openrtl_runtime_selftest(input logic a, b, output logic y);\n"
            "  assign y = a ^ b;\nendmodule\n"),
        "model/test_runtime.py": '''import os
from pathlib import Path
import unittest

class RuntimeTest(unittest.TestCase):
    def test_truth_table(self):
        self.assertEqual([a ^ b for a, b in ((0, 0), (0, 1), (1, 0), (1, 1))], [0, 1, 1, 0])

    def test_isolation(self):
        self.assertNotEqual(os.getuid(), 0)
        status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        self.assertEqual(int(status["CapEff"].strip(), 16), 0)
        self.assertEqual(status["NoNewPrivs"].strip(), "1")
        self.assertEqual(set(os.listdir("/sys/class/net")), {"lo"})
        self.assertFalse(any("KEY" in key or "TOKEN" in key or "SECRET" in key for key in os.environ))
        self.assertEqual(set(os.listdir("/control")), {"request.json", "run.py"})
        root_mount = [line.split() for line in Path("/proc/mounts").read_text().splitlines() if line.split()[1] == "/"]
        self.assertEqual(len(root_mount), 1)
        self.assertIn("ro", root_mount[0][3].split(","))
        with self.assertRaises(OSError):
            Path("/openrtl-selftest-write").write_text("readonly-check")
        cgroup = Path("/sys/fs/cgroup")
        self.assertLessEqual(int((cgroup / "memory.max").read_text()), MEMORY_BYTES)
        self.assertLessEqual(int((cgroup / "pids.max").read_text()), PIDS)
        quota, period = (cgroup / "cpu.max").read_text().split()
        self.assertLessEqual(int(quota) / int(period), CPUS)
'''.replace("MEMORY_BYTES", str(limits["memory_mib"] * 1024 * 1024))
            .replace("PIDS", str(limits["pids"])).replace("CPUS", str(limits["cpus"])),
        "dv/test_runtime.py": '''import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def runtime_truth_table(dut):
    for a, b, expected in ((0, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 0), (0, 1, 1)):
        dut.a.value = a
        dut.b.value = b
        await Timer(2, unit="ns")
        assert int(dut.y.value) == expected
    await Timer(2, unit="ns")
''',
    }
    manifest = {"top": "openrtl_runtime_selftest", "sources": ["rtl/openrtl_runtime_selftest.sv"],
                "test_modules": ["test_runtime"], "expected_tests": ["runtime_truth_table"], "seed": 1}
    return files, manifest


def selftest_digest(profile: JsonObject) -> str:
    files, manifest = collateral(profile)
    return content_digest({"files": files, "manifest": manifest})


def _read(path: Path, bound: int) -> bytes:
    candidate = safe_root(path)
    require(candidate.is_file() and candidate.stat().st_nlink == 1 and candidate.stat().st_size <= bound,
            "runtime_evidence_file_invalid")
    data = candidate.read_bytes()
    require(len(data) <= bound, "runtime_evidence_file_invalid")
    return data


def verify_evidence(project: Path, profile: JsonObject, operation: str, report: JsonObject) -> None:
    """Rehash all artifacts and fixed inputs; a report-shaped object is insufficient."""
    import re
    require(re.fullmatch(r"[a-f0-9]{32}", operation) is not None, "runtime_operation_invalid")
    files, manifest = collateral(profile)
    require(report.get("schema") == "openrtl.design-simulation.v2" and report.get("status") == "passed" and
            report.get("evidence_kind") == "isolated_verilator_cocotb" and report.get("error_code") is None and
            report.get("run_id") == operation and report.get("input_digest") == selftest_digest(profile) and
            report.get("tests") == manifest["expected_tests"] and report.get("model_tests") == 2,
            "runtime_selftest_not_passed")
    runner = Path(__file__).with_name("_design_runner.py")
    require(report.get("runtime") == {"profile_digest": content_digest(profile),
                                      "runner_digest": "sha256:" + hashlib.sha256(_read(runner, 64000)).hexdigest()},
            "runtime_selftest_binding_changed")
    run = safe_root(project / "runs" / operation)
    require(json.loads(_read(run / "intent.json", 64000)) == {
        "schema": "openrtl.design-runtime-intent.v1", "operation_id": operation,
        "container_name": "openrtl-design-" + operation, "profile_digest": content_digest(profile),
        "input_digest": selftest_digest(profile)}, "runtime_selftest_intent_changed")
    require({path.relative_to(run / "input").as_posix() for path in (run / "input").rglob("*")
             if not path.is_dir()} == set(files), "runtime_selftest_input_manifest_changed")
    for name, content in files.items():
        require(_read(run / "input" / name, 64000) == content.encode(), "runtime_selftest_inputs_changed")
    require(_read(run / "control/run.py", 64000) == _read(runner, 64000), "runtime_selftest_runner_changed")
    request = json.loads(_read(run / "control/request.json", 64000))
    require(request == {"manifest": manifest, "verilator_version": profile["verilator_version"],
                        "timeout_seconds": profile["timeout_seconds"] - 5}, "runtime_selftest_request_changed")
    artifacts = report.get("artifacts")
    expected = {"results.xml", "model-results.json", "waves.vcd", "runner.log", "toolchain.json"}
    require(isinstance(artifacts, dict) and set(artifacts) == expected, "runtime_evidence_incomplete")
    assert isinstance(artifacts, dict)
    data: dict[str, bytes] = {}
    for name in expected:
        row = artifacts[name]
        relative = "runs/" + operation + "/evidence/" + name
        require(isinstance(row, dict) and set(row) == {"path", "sha256", "bytes"} and row["path"] == relative,
                "runtime_evidence_path_invalid")
        payload = _read(project / relative, 16 * 1024 * 1024)
        require(type(row["bytes"]) is int and row["bytes"] == len(payload) and
                row["sha256"] == hashlib.sha256(payload).hexdigest(), "runtime_evidence_changed")
        data[name] = payload
    from openrtl.adapters.design_simulation import parse_test_results
    parse_test_results(data["results.xml"], manifest["expected_tests"])
    require(json.loads(data["model-results.json"]) == {"passed": True, "tests": 2}, "runtime_model_checks_failed")
    toolchain = json.loads(data["toolchain.json"])
    require(isinstance(toolchain, dict) and set(toolchain) == {"cocotb", "verilator"} and
            toolchain["cocotb"] == "2.0.1" and isinstance(toolchain["verilator"], str) and
            toolchain["verilator"].split()[:2] == ["Verilator", profile["verilator_version"]],
            "runtime_toolchain_changed")
    trace = VcdIndex.parse(data["waves.vcd"].decode("utf-8"))
    signals = [name for name in trace.signal_names if name.endswith(".y")]
    require(bool(signals) and all({row.value for row in trace.transitions(name)} >= {"0", "1"} and
                                 len(trace.transitions(name)) >= 3 for name in signals),
            "runtime_selftest_waveform_not_meaningful")
