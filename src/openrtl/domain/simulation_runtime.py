"""Explicit runtime identities and bounded resources for simulation, never consent."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from openrtl.domain.design_session import JsonObject, object_value, require


PROFILE_SCHEMA = "openrtl.design-container.v2"
RESOURCE_DEFAULTS = {"cpus": 2, "memory_mib": 2048, "pids": 128, "output_mib": 256}


def absolute_path(value: object) -> str:
    require(type(value) is str, "runtime_path_invalid")
    assert isinstance(value, str)
    path = PurePosixPath(value)
    require(path.is_absolute() and str(path) == value and ".." not in path.parts and
            all(ord(c) >= 32 and ord(c) != 127 for c in value) and "," not in value,
            "runtime_path_invalid")
    return value


def digest(value: object) -> str:
    require(isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value) is not None,
            "runtime_digest_invalid")
    assert isinstance(value, str)
    return value


def resources(value: object) -> JsonObject:
    result = object_value(value, set(RESOURCE_DEFAULTS))
    for key, low, high in (("cpus", 1, 8), ("memory_mib", 1024, 8192),
                           ("pids", 32, 256), ("output_mib", 128, 1024)):
        require(type(result[key]) is int and low <= result[key] <= high, "runtime_resource_bound_invalid")
    require(result["output_mib"] <= result["memory_mib"] // 2, "runtime_output_memory_invalid")
    return dict(result)


def validate_profile(value: object) -> JsonObject:
    result = object_value(value, {"schema", "docker_executable", "socket", "image_id", "python_executable",
                                  "verilator_version", "timeout_seconds", "architecture", "resources",
                                  "docker_sha256", "socket_identity", "daemon_id", "ownership"})
    require(result["schema"] == PROFILE_SCHEMA, "runtime_profile_schema_invalid")
    for key in ("docker_executable", "socket", "python_executable"):
        absolute_path(result[key])
    for key in ("image_id", "docker_sha256"):
        digest(result[key])
    require(result["architecture"] in ("amd64", "arm64"), "runtime_architecture_unsupported")
    require(result["ownership"] == "current-user-rootless", "runtime_ownership_unqualified")
    require(isinstance(result["daemon_id"], str) and
            re.fullmatch(r"[A-Za-z0-9:-]{1,128}", result["daemon_id"]) is not None, "runtime_daemon_id_invalid")
    require(isinstance(result["verilator_version"], str) and
            re.fullmatch(r"[0-9]+\.[0-9]+", result["verilator_version"]) is not None, "verilator_version_pin_required")
    require(type(result["timeout_seconds"]) is int and 10 <= result["timeout_seconds"] <= 600,
            "simulation_timeout_invalid")
    identity = object_value(result["socket_identity"], {"device", "inode", "uid"})
    require(all(type(n) is int and n >= 0 for n in identity.values()), "runtime_socket_identity_invalid")
    return {**result, "resources": resources(result["resources"]), "socket_identity": dict(identity)}


def validate_observation(profile: JsonObject, daemon: object, image: object) -> None:
    """Only narrow Docker fields enter this boundary; raw daemon data is not retained."""
    info = object_value(daemon, {"id", "os", "architecture", "rootless", "cpus", "memory_bytes"})
    selected = object_value(image, {"id", "os", "architecture"})
    require(info["id"] == profile["daemon_id"] and info["rootless"] is True and info["os"] == "linux",
            "runtime_daemon_identity_or_ownership_changed")
    require(info["architecture"] == profile["architecture"] and selected == {
        "id": profile["image_id"], "os": "linux", "architecture": profile["architecture"]},
        "runtime_image_or_architecture_changed")
    limits = resources(profile["resources"])
    require(type(info["cpus"]) is int and info["cpus"] >= limits["cpus"] and
            type(info["memory_bytes"]) is int and info["memory_bytes"] >= limits["memory_mib"] * 1024 * 1024,
            "runtime_resources_unavailable")


def resource_arguments(profile: JsonObject) -> list[str]:
    selected = resources(profile["resources"]) if profile.get("schema") == PROFILE_SCHEMA else RESOURCE_DEFAULTS
    return ["--pids-limit=" + str(selected["pids"]), "--memory=" + str(selected["memory_mib"]) + "m",
            "--cpus=" + str(selected["cpus"]),
            "--tmpfs=/output:rw,nosuid,nodev,exec,size=" + str(selected["output_mib"]) + "m,mode=1777"]


def validate_receipt(value: object) -> JsonObject:
    require(isinstance(value, dict), "runtime_receipt_invalid")
    assert isinstance(value, dict)
    fields = {"schema", "attempt", "operation", "profile_digest", "selftest_digest", "status"}
    if value.get("status") == "passed":
        fields.add("report_digest")
    result = object_value(value, fields)
    require(result["schema"] == "openrtl.runtime-selftest.v1" and
            result["status"] in ("not-started", "running", "needs-recovery", "passed", "recovered"),
            "runtime_receipt_invalid")
    for key in ("attempt", "operation"):
        require(isinstance(result[key], str) and re.fullmatch(r"[a-f0-9]{32}", result[key]) is not None,
                "runtime_receipt_invalid")
    for key in ("profile_digest", "selftest_digest"):
        digest(result[key])
    if result["status"] == "passed":
        digest(result["report_digest"])
    return dict(result)
