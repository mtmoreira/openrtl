"""Real shell negative paths and synthetic cache receipts; no runtime installation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from tools.bootstrap_openrtl import ROOT


class RuntimeShellTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "private product state"
        machine = (platform.system(), platform.machine())
        if machine in (("Darwin", "arm64"), ("Darwin", "aarch64")):
            self.target = "aarch64-apple-darwin"
            self.request = "cpython-3.13.15-macos-aarch64-none"
        elif machine == ("Linux", "x86_64"):
            self.target = "x86_64-unknown-linux-gnu"
            self.request = "cpython-3.13.15-linux-x86_64-gnu"
        else:
            self.skipTest("runtime candidate targets macOS arm64/Linux glibc x86-64 only")

    def run_helper(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/bin/sh", str(ROOT / "tools/bootstrap_runtime.sh"),
                               "--state-dir", str(self.state), *arguments],
                              env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=10, check=False)

    def test_batch_denial_and_missing_offline_artifacts_leave_no_state(self) -> None:
        for arguments, expected in ((["batch"], "runtime_consent_required"),
                                     (["--allow-runtime-install", "--offline"], "offline_artifacts_required")):
            with self.subTest(arguments=arguments):
                result = self.run_helper(*arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertFalse(self.state.exists())

    def test_linked_or_public_state_is_rejected_without_permission_changes(self) -> None:
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        result = self.run_helper("--allow-runtime-install", "--offline")
        self.assertEqual(result.returncode, 2)
        self.assertIn("private_owned_directory_required", result.stderr)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o755)
        self.state.rmdir()
        self.state.symlink_to(self.root)
        result = self.run_helper()
        self.assertEqual(result.returncode, 2)
        self.assertIn("linked_path", result.stderr)

    def test_corrupt_offline_uv_is_rejected_before_extraction_or_execution(self) -> None:
        artifacts = self.root / "offline artifacts"
        artifacts.mkdir()
        (artifacts / ("uv-" + self.target + ".tar.gz")).write_bytes(b"synthetic invalid archive")
        result = self.run_helper("--allow-runtime-install", "--offline", "--runtime-artifacts", str(artifacts))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("artifact_hash_mismatch", result.stderr)
        base = self.state / "runtime" / ("uv-0.12.3-" + self.target)
        self.assertFalse((base / "setup.lock").exists())
        self.assertFalse(any(base.glob("tool.*")))
        self.assertFalse(any(base.glob("python.*")))
        self.assertEqual(len(list(base.glob("artifact.*"))), 1)

    def test_active_receipt_selects_only_private_completed_attempt_without_executing_it(self) -> None:
        base = self.state / "runtime" / ("uv-0.12.3-" + self.target)
        attempt = base / "python.0123456789"
        binary = attempt / "install" / self.request / "bin" / "python3.13"
        binary.parent.mkdir(parents=True, mode=0o700)
        # mkdir(parents=True) uses default modes for intermediates.
        for directory in (self.state, self.state / "runtime", base, attempt):
            directory.chmod(0o700)
        binary.write_text("#!/bin/sh\nexit 97\n")
        binary.chmod(0o700)
        (attempt / "complete").write_text("synthetic setup receipt; not a Python runtime\n")
        (attempt / "complete").chmod(0o600)
        (base / "python-active").write_text(attempt.name + "\n")
        (base / "python-active").chmod(0o600)
        result = self.run_helper("--offline", "doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(binary))
        self.assertFalse((base / "setup.lock").exists())
        (base / "python-active").write_text("../../outside\n")
        result = self.run_helper("--offline", "doctor")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid_runtime_receipt", result.stderr)

    def test_held_setup_lock_is_preserved_and_never_kills_an_existing_process(self) -> None:
        base = self.state / "runtime" / ("uv-0.12.3-" + self.target)
        lock = base / "setup.lock"
        lock.mkdir(parents=True, mode=0o700)
        for directory in (self.state, self.state / "runtime", base):
            directory.chmod(0o700)
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        result = self.run_helper("--allow-runtime-install", "--offline", "--runtime-artifacts", str(artifacts))
        self.assertEqual(result.returncode, 2)
        self.assertIn("setup_locked", result.stderr)
        self.assertTrue(lock.is_dir())

    def test_shell_pins_match_reviewed_provenance(self) -> None:
        document = json.loads((ROOT / "bootstrap/runtime-provenance.json").read_bytes())
        script = (ROOT / "tools/bootstrap_runtime.sh").read_text()
        for target, row in document["targets"].items():
            self.assertIn(target, script)
            for field in ("uv_sha256", "python_sha256", "python_request"):
                self.assertIn(row[field], script)
        self.assertFalse(document["qualification"]["binary_downloads_performed"])


if __name__ == "__main__":
    unittest.main()
