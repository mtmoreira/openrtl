"""Compare recorded simulation workload observations, not synthesis or equivalence."""
from __future__ import annotations

from decimal import Decimal

from openrtl.domain.design_imports import digest_value
from openrtl.domain.design_coaching import simulation_duration
from openrtl.domain.design_session import JsonObject, content_digest, object_value, require


def compare_runs(before: JsonObject, after: JsonObject, left: JsonObject, right: JsonObject) -> JsonObject:
    # Measurement extraction belongs to the adapter; here we bind the supplied
    # measured records to two immutable, current-for-their-input run snapshots.
    for state, measurement in ((before, left), (after, right)):
        record = object_value(measurement, {"schema", "input_digest", "run_id", "profile_digest", "runner_digest",
                                            "results_digest", "per_test_ns"})
        expected = content_digest({"spec": state["approved_spec"], "files": state["files"], "manifest": state["manifest"]})
        simulation = state["simulation"]
        require(record["schema"] == "openrtl.design-measurement.v1" and simulation is not None and
                simulation["status"] == "passed" and simulation["evidence_kind"] == "isolated_verilator_cocotb" and
                simulation["input_digest"] == record["input_digest"] == expected and
                simulation["run_id"] == record["run_id"], "comparison_run_binding_invalid")
        for key in ("profile_digest", "runner_digest", "results_digest"):
            digest_value(record[key])
        require(simulation.get("schema") == "openrtl.design-simulation.v2" and
                simulation["runtime"] == {"profile_digest": record["profile_digest"], "runner_digest": record["runner_digest"]} and
                record["results_digest"] == "sha256:" + simulation["artifacts"]["results.xml"]["sha256"],
                "comparison_measurement_binding_invalid")
        require(isinstance(record["per_test_ns"], dict) and set(record["per_test_ns"]) == set(simulation["tests"]) ==
                set(state["manifest"]["expected_tests"]), "comparison_measurement_tests_invalid")
        for value in record["per_test_ns"].values():
            simulation_duration(value)
    require(left["run_id"] != right["run_id"], "comparison_requires_distinct_runs")
    reasons: list[str] = []
    if before["spec"] != after["spec"]:
        reasons.append("requirements_changed")
    if before["manifest"] != after["manifest"]:
        reasons.append("simulation_manifest_changed")
    def readonly(s: JsonObject) -> dict[str, str]:
        return {p: h for p, h in s["files"].items() if not p.startswith("rtl/")}
    if readonly(before) != readonly(after):
        reasons.append("non_rtl_collateral_changed")
    if left["profile_digest"] != right["profile_digest"] or left["runner_digest"] != right["runner_digest"]:
        reasons.append("runtime_changed")
    delta = None if reasons else {test: str(Decimal(right["per_test_ns"][test]) - Decimal(value))
                                  for test, value in left["per_test_ns"].items()}
    old_tests, new_tests = set(before["manifest"]["expected_tests"]), set(after["manifest"]["expected_tests"])
    return {"schema": "openrtl.design-comparison.v1", "status": "not_comparable" if reasons else "observations_comparable",
            "reasons": reasons, "baseline_revision": before["revision"], "candidate_revision": after["revision"],
            "baseline": left, "candidate": right, "per_test_delta_ns": delta,
            "declared_tests_added": sorted(new_tests - old_tests), "declared_tests_removed": sorted(old_tests - new_tests),
            "changed_paths": sorted(p for p in set(before["files"]) | set(after["files"])
                                     if before["files"].get(p) != after["files"].get(p)),
            "limits": ["Recorded simulated durations, not host benchmark time or hardware PPA.",
                       "RTL, including assertion sources, may differ; functional equivalence is not established.",
                       "Declared tests and passing examples are not measured functional coverage or exhaustive proof.",
                       "Generated test outputs are not independent attestation; candidate signoff and review still apply."]}
