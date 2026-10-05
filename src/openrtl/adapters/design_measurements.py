"""Reverify owned run artifacts before extracting simulation-time measurements."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from openrtl.adapters.design_session_store import safe_root
from openrtl.adapters.design_simulation import parse_test_results
from openrtl.domain.design_imports import digest_value
from openrtl.domain.design_coaching import simulation_duration
from openrtl.domain.design_session import JsonObject, object_value, require


def simulation_times(data: bytes, expected: list[str]) -> dict[str, str]:
    parse_test_results(data, expected)
    cases = ET.fromstring(data).iter("testcase")
    result = {}
    for case in cases:
        # Wall-clock 'time' is not a substitute for simulated duration.
        value = case.attrib.get("sim_time_ns")
        require(value is not None, "simulation_time_measurement_missing")
        result[case.attrib["name"]] = str(simulation_duration(value))
    return result


def _read(path: Path, bound: int) -> bytes:
    selected = safe_root(path)
    require(selected.is_file() and selected.stat().st_size <= bound, "measurement_artifact_unavailable")
    data = selected.read_bytes()
    require(len(data) <= bound, "measurement_artifact_exceeds_bound")
    return data


def measured_run(root: Path, state: JsonObject) -> JsonObject:
    report = state["simulation"]
    require(isinstance(report, dict) and report.get("status") == "passed" and
            report.get("evidence_kind") == "isolated_verilator_cocotb", "comparison_requires_passing_run")
    require(report["schema"] == "openrtl.design-simulation.v2", "comparison_requires_runtime_bound_run")
    runtime = object_value(report["runtime"], {"profile_digest", "runner_digest"})
    run_id = report["run_id"]
    require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id) is not None, "measurement_run_id_invalid")
    run = safe_root(root / "runs" / run_id)
    require(json.loads(_read(run / "evidence/report.json", 64000)) == report, "measurement_report_changed")
    intent = object_value(json.loads(_read(run / "intent.json", 4096)),
                          {"schema", "operation_id", "container_name", "profile_digest", "input_digest"})
    require(intent["schema"] == "openrtl.design-runtime-intent.v1" and intent["operation_id"] == run_id and
            intent["container_name"] == "openrtl-design-" + run_id and intent["input_digest"] == report["input_digest"],
            "measurement_intent_changed")
    digest_value(intent["profile_digest"])
    runner_digest = "sha256:" + hashlib.sha256(_read(run / "control/run.py", 256 * 1024)).hexdigest()
    require(runtime == {"profile_digest": intent["profile_digest"], "runner_digest": runner_digest},
            "measurement_runtime_changed")
    expected = {"results.xml", "model-results.json", "waves.vcd", "runner.log", "toolchain.json"}
    require(isinstance(report["artifacts"], dict) and set(report["artifacts"]) == expected, "measurement_artifact_set_invalid")
    decoded: dict[str, bytes] = {}
    for filename, value in report["artifacts"].items():
        entry = object_value(value, {"sha256", "bytes", "path"})
        require(entry["path"] == "runs/" + run_id + "/evidence/" + filename, "measurement_path_changed")
        data = _read(run / "evidence" / filename, 16 * 1024 * 1024)
        require(type(entry["bytes"]) is int and len(data) == entry["bytes"] and
                hashlib.sha256(data).hexdigest() == entry["sha256"], "measurement_artifact_changed")
        decoded[filename] = data
    times = simulation_times(decoded["results.xml"], state["manifest"]["expected_tests"])
    require(set(times) == set(report["tests"]), "measurement_test_binding_invalid")
    return {"schema": "openrtl.design-measurement.v1", "input_digest": report["input_digest"], "run_id": run_id,
            "profile_digest": intent["profile_digest"],
            "runner_digest": runner_digest,
            "results_digest": "sha256:" + hashlib.sha256(decoded["results.xml"]).hexdigest(), "per_test_ns": times}
