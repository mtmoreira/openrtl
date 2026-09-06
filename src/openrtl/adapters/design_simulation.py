"""Opt-in local container execution of untrusted design collateral, never a shell."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

from openrtl.adapters.design_session_store import safe_root
from openrtl.domain.design_session import JsonObject, canonical, object_value, require, source_path


def parse_test_results(data: bytes, expected: list[str]) -> list[str]:
    require(len(data) <= 2 * 1024 * 1024 and b"<!DOCTYPE" not in data.upper() and
            b"<!ENTITY" not in data.upper(), "test_xml_invalid")
    root = ET.fromstring(data)
    require(root.tag in ("testsuites", "testsuite"), "test_xml_root_invalid")
    cases = list(root.iter("testcase"))
    require(bool(cases) and all(not any(c.tag in ("failure", "error", "skipped") for c in case)
                              for case in cases), "simulation_tests_failed_or_skipped")
    names = [case.attrib.get("name", "") for case in cases]
    require(len(names) == len(set(names)) and set(names) == set(expected), "simulation_test_manifest_mismatch")
    for suite in root.iter("testsuite"):
        require(all(suite.attrib.get(k, "0") in ("0", "0.0") for k in ("failures", "errors", "skipped")),
                "simulation_suite_failed")
    return names


class IsolatedDesignSimulator:
    def __init__(self, project: Path, profile: object) -> None:
        self.project = safe_root(project)
        selected = object_value(profile, {"schema", "docker_executable", "socket", "image_id",
                                          "python_executable", "verilator_version", "timeout_seconds"})
        require(selected["schema"] == "openrtl.design-container.v1", "simulation_profile_unrecognized")
        for key in ("docker_executable", "socket", "python_executable"):
            require(isinstance(selected[key], str) and selected[key].startswith("/") and
                    "\x00" not in selected[key] and ".." not in Path(selected[key]).parts,
                    "simulation_profile_path_invalid")
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", selected["image_id"]) is not None, "local_image_pin_required")
        require(isinstance(selected["verilator_version"], str) and
                re.fullmatch(r"[0-9]+\.[0-9]+", selected["verilator_version"]) is not None,
                "verilator_version_pin_required")
        require(type(selected["timeout_seconds"]) is int and 10 <= selected["timeout_seconds"] <= 600,
                "simulation_timeout_invalid")
        require(Path(selected["docker_executable"]).is_file() and os.access(selected["docker_executable"], os.X_OK),
                "selected_docker_unavailable")
        require(stat.S_ISSOCK(Path(selected["socket"]).stat().st_mode), "selected_local_socket_unavailable")
        self.profile = selected

    async def _process(self, argv: list[str], config: Path, timeout: int,
                       bound: int = 48 * 1024 * 1024) -> tuple[int, bytes]:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={"PATH": "/usr/bin:/bin", "DOCKER_CONFIG": str(config), "LANG": "C", "LC_ALL": "C"},
            cwd=self.project,
        )
        async def read() -> bytes:
            assert process.stdout is not None
            chunks = []
            size = 0
            while chunk := await process.stdout.read(65536):
                size += len(chunk)
                require(size <= bound, "simulation_output_limit")
                chunks.append(chunk)
            await process.wait()
            return b"".join(chunks)
        try:
            data = await asyncio.wait_for(read(), timeout=timeout)
            assert process.returncode is not None
            return process.returncode, data
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def simulate(self, files: dict[str, str], manifest: JsonObject,
                       input_digest: str, operation_id: str) -> JsonObject:
        require(re.fullmatch(r"[a-f0-9]{32}", operation_id) is not None, "simulation_operation_invalid")
        parent = safe_root(self.project / "runs")
        parent.mkdir(mode=0o700, exist_ok=True)
        run = parent / operation_id
        require(not run.exists(), "simulation_run_already_exists")
        run.mkdir(mode=0o700)
        inputs, control, config = run / "input", run / "control", run / "docker-config"
        for directory in (inputs, control, config):
            directory.mkdir(mode=0o755 if directory != config else 0o700)
        for relative, content in files.items():
            path = inputs / source_path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
        runtime = {"manifest": manifest, "verilator_version": self.profile["verilator_version"],
                   "timeout_seconds": self.profile["timeout_seconds"] - 5}
        (control / "request.json").write_bytes(canonical(runtime))
        runner = Path(__file__).with_name("_design_runner.py")
        (control / "run.py").write_bytes(runner.read_bytes())
        prefix = [self.profile["docker_executable"], "--host", "unix://" + self.profile["socket"],
                  "--config", str(config)]
        # A literal local image ID and --pull=never prohibit implicit downloads.
        argv = prefix + ["create", "--pull=never", "--network=none", "--read-only",
                         "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128",
                         "--memory=2g", "--cpus=2", "--user=65534:65534",
                         "--tmpfs=/output:rw,nosuid,nodev,exec,size=256m,mode=1777",
                         "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                         "--mount", "type=bind,src=" + str(inputs) + ",dst=/input,readonly",
                         "--mount", "type=bind,src=" + str(control) + ",dst=/control,readonly",
                         "--workdir=/output", "--env=HOME=/tmp", "--env=PYTHONNOUSERSITE=1",
                         "--entrypoint", self.profile["python_executable"], self.profile["image_id"],
                         "-I", "/control/run.py"]
        require("," not in str(run), "container_mount_path_invalid")
        code, created = await self._process(argv, config, 30, 8192)
        container = created.decode("ascii", errors="replace").strip()
        require(code == 0 and re.fullmatch(r"[a-f0-9]{64}", container) is not None, "container_creation_failed")
        try:
            code, raw = await self._process(prefix + ["start", "--attach", container], config,
                                             self.profile["timeout_seconds"])
            require(code == 0, "isolated_runner_failed")
            payload = object_value(json.loads(raw), {"status", "model_tests", "artifacts", "error_code"})
            require(payload["status"] in ("passed", "failed"), "isolated_runner_status_invalid")
            require(isinstance(payload["artifacts"], dict), "isolated_artifact_manifest_invalid")
            allowed = {"results.xml", "model-results.json", "waves.vcd", "runner.log", "toolchain.json"}
            require(set(payload["artifacts"]).issubset(allowed), "isolated_artifact_path_invalid")
            artifact_digests: JsonObject = {}
            decoded: dict[str, bytes] = {}
            output = run / "evidence"
            output.mkdir(mode=0o700)
            for filename, encoded in payload["artifacts"].items():
                require(isinstance(encoded, str) and len(encoded) <= 24 * 1024 * 1024, "isolated_artifact_bound")
                data = base64.b64decode(encoded, validate=True)
                require(len(data) <= 16 * 1024 * 1024, "isolated_artifact_bound")
                with (output / filename).open("xb") as stream:
                    stream.write(data)
                decoded[filename] = data
                artifact_digests[filename] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                                               "path": "runs/" + operation_id + "/evidence/" + filename}
            tests: list[str] = []
            if payload["status"] == "passed":
                require(set(decoded) == allowed, "isolated_evidence_missing")
                try:
                    tests = parse_test_results(decoded["results.xml"], manifest["expected_tests"])
                except (ValueError, ET.ParseError):
                    payload["status"] = "failed"
                    payload["error_code"] = "isolated_tests_or_build_failed"
                model_result = object_value(json.loads(decoded["model-results.json"]), {"tests", "passed"})
                require(model_result["passed"] is True and type(model_result["tests"]) is int and
                        model_result["tests"] > 0 and model_result["tests"] == payload["model_tests"],
                        "model_test_evidence_invalid")
                require(b"$enddefinitions" in decoded["waves.vcd"] and b"#" in decoded["waves.vcd"],
                        "waveform_evidence_invalid")
            require(payload["error_code"] in (None, "isolated_tests_or_build_failed"), "runner_error_code_invalid")
            report: JsonObject = {"schema": "openrtl.design-simulation.v1", "input_digest": input_digest,
                                  "status": payload["status"], "evidence_kind": "isolated_verilator_cocotb",
                                  "run_id": operation_id, "tests": tests, "model_tests": payload["model_tests"],
                                  "artifacts": artifact_digests, "error_code": payload["error_code"],
                                  "diagnostics": decoded.get("runner.log", b"")[-8000:].decode("utf-8", errors="replace")}
            (output / "report.json").write_bytes(canonical(report))
            return report
        finally:
            removed, _ = await self._process(prefix + ["rm", "--force", container], config, 30, 8192)
            require(removed == 0, "owned_container_cleanup_requires_reconciliation")
