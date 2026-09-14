"""Strict, backend-neutral source contract for the managed simulator image."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from openrtl.domain.design_session import JsonObject, object_value, require, sequence


SOURCE_SCHEMA = "openrtl.simulator-image-source.v1"
SOURCE_RELATIVE_PATH = "simulation/image-source.json"
_HEX_DIGEST = re.compile(r"[a-f0-9]{64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,2}")
_EXPECTED_BASE = {
    "repository": "docker.io/verilator/verilator",
    "tag": "v5.046",
    "index_digest": "sha256:8003e282e9ff02eb446083eb5a3c512225a269f0a7b46a7e392c99468e890018",
    "manifest_digest": "sha256:00403822bc4edb5b35b630f72502de5c83fa8ba1b6a8e223045db28f5311ffb0",
    "maximum_compressed_bytes": 300000000,
}
_EXPECTED_ARTIFACTS = {
    "cocotb": {
        "version": "2.0.1",
        "distribution": "sdist",
        "filename": "cocotb-2.0.1.tar.gz",
        "url": "https://files.pythonhosted.org/packages/28/f1/40b6086c1bba60cc7c59ff40815b35a5c364711d1ee86c878a296b0a963b/cocotb-2.0.1.tar.gz",
        "sha256": "69887748412ff43e98f8579ad6c0da1f6ff19a94d0c3b4d6da472d8e86784e82",
        "size": 318250,
    },
    "find-libpython": {
        "version": "0.5.1",
        "distribution": "wheel",
        "filename": "find_libpython-0.5.1-py3-none-any.whl",
        "url": "https://files.pythonhosted.org/packages/34/1f/1d6079f4f0540aaa368aa20d89d98eda42f081c397a822c547340e32d1e3/find_libpython-0.5.1-py3-none-any.whl",
        "sha256": "723a8cfe6fed255a1f58b53c62ed556fb340ec0d456e9863ebc01a5cc047607d",
        "size": 9201,
    },
}


def _digest(value: object, *, prefixed: bool) -> str:
    require(isinstance(value, str), "simulator_image_digest_invalid")
    assert isinstance(value, str)
    candidate = value[7:] if prefixed and value.startswith("sha256:") else value
    require((not prefixed or value.startswith("sha256:")) and _HEX_DIGEST.fullmatch(candidate) is not None,
            "simulator_image_digest_invalid")
    return value


def _version(value: object) -> str:
    require(isinstance(value, str) and _VERSION.fullmatch(value) is not None,
            "simulator_image_version_invalid")
    assert isinstance(value, str)
    return value


def _artifact(value: object) -> JsonObject:
    result = object_value(value, {"name", "version", "distribution", "filename", "url", "sha256", "size"})
    require(result["name"] in _EXPECTED_ARTIFACTS, "simulator_image_artifact_unknown")
    _version(result["version"])
    require(result["distribution"] in ("sdist", "wheel"), "simulator_image_distribution_invalid")
    require(isinstance(result["filename"], str) and
            re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", result["filename"]) is not None,
            "simulator_image_filename_invalid")
    require(isinstance(result["url"], str), "simulator_image_url_invalid")
    assert isinstance(result["url"], str)
    parsed = urlsplit(result["url"])
    require(parsed.scheme == "https" and parsed.hostname == "files.pythonhosted.org" and
            parsed.username is None and parsed.password is None and parsed.port is None and
            not parsed.query and not parsed.fragment and parsed.path.endswith("/" + result["filename"]),
            "simulator_image_url_invalid")
    _digest(result["sha256"], prefixed=False)
    require(type(result["size"]) is int and 1 <= result["size"] <= 8 * 1024 * 1024,
            "simulator_image_artifact_size_invalid")
    expected = {"name": result["name"], **_EXPECTED_ARTIFACTS[result["name"]]}
    require(result == expected, "simulator_image_artifact_pin_changed")
    return dict(result)


def validate_source(value: object) -> JsonObject:
    """Validate the exact acquisition input without contacting a runtime or network."""
    source = object_value(value, {"schema", "purpose", "platform", "toolchain", "base_image",
                                  "python_artifacts", "acquisition_policy"})
    require(source["schema"] == SOURCE_SCHEMA and source["purpose"] == "acquisition-input-only",
            "simulator_image_source_schema_invalid")
    platform = object_value(source["platform"], {"os", "architecture"})
    require(platform == {"os": "linux", "architecture": "arm64"},
            "simulator_image_platform_invalid")
    toolchain = object_value(source["toolchain"], {"verilator", "cocotb", "python_minimum"})
    for version in toolchain.values():
        _version(version)
    require(toolchain == {"verilator": "5.046", "cocotb": "2.0.1", "python_minimum": "3.12"},
            "simulator_image_toolchain_pin_changed")
    base = object_value(source["base_image"], set(_EXPECTED_BASE))
    _digest(base["index_digest"], prefixed=True)
    _digest(base["manifest_digest"], prefixed=True)
    require(type(base["maximum_compressed_bytes"]) is int, "simulator_image_base_size_invalid")
    require(base == _EXPECTED_BASE and base["tag"] == "v" + toolchain["verilator"],
            "simulator_image_base_pin_changed")
    artifacts = [_artifact(item) for item in sequence(source["python_artifacts"], maximum=2)]
    require(len(artifacts) == 2 and [item["name"] for item in artifacts] == ["cocotb", "find-libpython"],
            "simulator_image_artifacts_invalid")
    require(artifacts[0]["version"] == toolchain["cocotb"], "simulator_image_cocotb_pin_changed")
    policy = object_value(source["acquisition_policy"], {"registry", "python_artifact_host",
                                                         "empty_registry_client_config",
                                                         "pull_by_manifest_digest", "run_containers",
                                                         "build_images", "install_packages"})
    require(policy == {"registry": "docker.io", "python_artifact_host": "files.pythonhosted.org",
                       "empty_registry_client_config": True, "pull_by_manifest_digest": True,
                       "run_containers": False, "build_images": False, "install_packages": False},
            "simulator_image_acquisition_policy_invalid")
    return {**source, "platform": dict(platform), "toolchain": dict(toolchain), "base_image": dict(base),
            "python_artifacts": artifacts, "acquisition_policy": dict(policy)}
