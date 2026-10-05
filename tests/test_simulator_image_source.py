"""Offline tests for the simulator image acquisition source contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from typing import cast

from openrtl.domain.simulator_image_source import SOURCE_RELATIVE_PATH, validate_source
from tools.validate_simulator_image_source import validate


ROOT = Path(__file__).resolve().parents[1]


def source() -> dict[str, object]:
    return cast(dict[str, object],
                json.loads((ROOT / SOURCE_RELATIVE_PATH).read_text(encoding="utf-8")))


class SimulatorImageSourceTest(unittest.TestCase):
    def test_checked_in_source_is_lock_bound_without_backend_authority(self) -> None:
        selected = validate_source(source())
        self.assertEqual(selected["platform"], {"os": "linux", "architecture": "arm64"})
        self.assertEqual(selected["toolchain"]["verilator"], "5.046")
        self.assertEqual(selected["base_image"]["manifest_digest"],
                         "sha256:00403822bc4edb5b35b630f72502de5c83fa8ba1b6a8e223045db28f5311ffb0")
        source_bytes = (ROOT / SOURCE_RELATIVE_PATH).read_bytes()
        self.assertEqual(validate(ROOT), "sha256:" + hashlib.sha256(source_bytes).hexdigest())
        serialized = json.dumps(selected, sort_keys=True).lower()
        for forbidden in ("lima", "socket", "docker_executable", "credential", "private_key"):
            self.assertNotIn(forbidden, serialized)

    def test_unknown_fields_and_mutated_pins_fail_closed(self) -> None:
        cases: list[dict[str, object]] = []
        unknown = source()
        unknown["backend"] = "synthetic"
        cases.append(unknown)
        platform = source()
        platform["platform"] = {"os": "linux", "architecture": "amd64"}
        cases.append(platform)
        base = source()
        assert isinstance(base["base_image"], dict)
        base["base_image"]["manifest_digest"] = "sha256:" + "0" * 64
        cases.append(base)
        artifact = source()
        assert isinstance(artifact["python_artifacts"], list)
        assert isinstance(artifact["python_artifacts"][0], dict)
        artifact["python_artifacts"][0]["url"] = "https://example.invalid/cocotb-2.0.1.tar.gz"
        cases.append(artifact)
        execution = source()
        assert isinstance(execution["acquisition_policy"], dict)
        execution["acquisition_policy"]["run_containers"] = True
        cases.append(execution)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_source(candidate)

    def test_lock_binding_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "simulation").mkdir()
            (root / SOURCE_RELATIVE_PATH).write_bytes((ROOT / SOURCE_RELATIVE_PATH).read_bytes())
            lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
            (root / "uv.lock").write_text(lock.replace("size = 318250", "size = 318251", 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lock_binding_changed"):
                validate(root)


if __name__ == "__main__":
    unittest.main()
