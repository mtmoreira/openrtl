"""Synthetic setup policy/cache tests. No installed SDK or provider qualification."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools import bootstrap_openrtl as bootstrap
from tools import bootstrap_sdk as sdk


class SDKSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "private state"
        self.wheels = self.root / "wheel directory"
        self.wheels.mkdir()
        self.artifacts = self.root / "runtime artifacts"
        self.artifacts.mkdir()
        self.commands: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(self, argv: list[str], cwd: Path, environment: dict[str, str], *,
                 timeout: int = 180, capture_path: bool = False) -> bytes:
        self.commands.append((argv, environment))
        if "--target" in argv:
            site = Path(argv[argv.index("--target") + 1])
            for name, version in sdk.locked_packages().items():
                metadata = site / (name.replace("-", "_") + "-" + version + ".dist-info")
                metadata.mkdir()
                (metadata / "METADATA").write_text("Name: " + name + "\nVersion: " + version + "\n")
            package = site / "openai"
            package.mkdir()
            (package / "__init__.py").write_text("__version__ = '2.47.0'\n")
            package = site / "ollama"
            package.mkdir()
            (package / "__init__.py").write_text("")
        return b""

    def install(self) -> Path:
        with patch.object(sdk, "prepare_uv", return_value=self.root / "synthetic uv"), \
                patch.object(sdk, "run_bounded", side_effect=self.fake_run):
            return sdk.install_sdk(self.state, authorized=True, offline=True,
                                   wheelhouse=self.wheels, artifacts=self.artifacts)

    def test_consent_and_missing_offline_artifacts_fail_before_state_or_processes(self) -> None:
        for authorized, offline, code in ((False, False, "consent_required"),
                                           (True, True, "offline_sdk_artifacts_required")):
            with self.subTest(code=code), patch.object(sdk, "prepare_uv") as prepare:
                with self.assertRaisesRegex(bootstrap.BootstrapError, code):
                    sdk.install_sdk(self.state, authorized=authorized, offline=offline)
                prepare.assert_not_called()
                self.assertFalse(self.state.exists())

    def test_exact_lock_is_verified_and_changed_lock_is_rejected(self) -> None:
        self.assertEqual(len(sdk.locked_packages()), 17)
        self.assertEqual(sdk.locked_packages()["openai"], "2.47.0")
        self.assertEqual(sdk.locked_packages()["ollama"], "0.6.2")
        changed = self.root / "changed.lock"
        changed.write_bytes(sdk.LOCK.read_bytes() + b"\n")
        with patch.object(sdk, "LOCK", changed), self.assertRaisesRegex(bootstrap.BootstrapError, "sdk_lock_changed"):
            sdk.locked_packages()

    def test_setup_is_offline_hashed_hook_free_isolated_and_reusable(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-never-forward", "UV_INDEX_URL": "synthetic-never-forward"}):
            site = self.install()
        self.assertEqual(sdk.cached_sdk(self.state), site)
        self.assertEqual(len(self.commands), 2)
        command, environment = self.commands[0]
        for argument in ("--offline", "--no-config", "--no-python-downloads", "--no-index", "--require-hashes", "--no-deps"):
            self.assertIn(argument, command)
        self.assertEqual(command[command.index("--only-binary") + 1], ":all:")
        self.assertEqual(command[command.index("--link-mode") + 1], "copy")
        self.assertEqual(command[command.index("--requirements") + 1], str(sdk.LOCK))
        import_command, import_environment = self.commands[1]
        self.assertIn("import openai, ollama", import_command[5])
        self.assertIn("version('ollama') == '0.6.2'", import_command[5])
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("UV_INDEX_URL", environment)
        self.assertNotIn("OPENAI_API_KEY", import_environment)
        self.assertNotIn("UV_INDEX_URL", import_environment)
        self.assertEqual(environment["UV_PYTHON_DOWNLOADS"], "never")
        for item in site.rglob("*"):
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o700 if item.is_dir() else 0o600)
        before = (site.parent.parent / "active.json").read_bytes()
        with patch.object(sdk, "prepare_uv", side_effect=AssertionError("cached setup must not prepare a tool")):
            self.assertEqual(sdk.install_sdk(self.state, authorized=True, offline=True), site)
        self.assertEqual((site.parent.parent / "active.json").read_bytes(), before)

    def test_tampered_missing_extra_or_linked_cache_is_never_reinstalled_silently(self) -> None:
        site = self.install()
        target = site / "openai/__init__.py"
        original = target.read_bytes()
        for kind in ("changed", "missing", "extra", "linked"):
            with self.subTest(kind=kind):
                extra = site / "unexpected.py"
                if kind == "changed":
                    target.write_bytes(original + b"# changed\n")
                elif kind == "missing":
                    target.unlink()
                elif kind == "extra":
                    extra.write_text("unreviewed = True\n")
                    extra.chmod(0o600)
                else:
                    target.unlink()
                    target.symlink_to(self.root / "unread-target")
                with patch.object(sdk, "prepare_uv") as prepare:
                    with self.assertRaises((bootstrap.BootstrapError, OSError)):
                        sdk.install_sdk(self.state, authorized=True, offline=True)
                    prepare.assert_not_called()
                if os.path.lexists(target):
                    target.unlink()
                target.write_bytes(original)
                target.chmod(0o600)
                if extra.exists():
                    extra.unlink()

    def test_interrupted_attempt_is_retained_and_retry_uses_fresh_attempt(self) -> None:
        with patch.object(sdk, "prepare_uv", return_value=self.root / "synthetic uv"), \
                patch.object(sdk, "run_bounded", side_effect=TimeoutError("synthetic interruption")):
            with self.assertRaises(TimeoutError):
                sdk.install_sdk(self.state, authorized=True, offline=True,
                                wheelhouse=self.wheels, artifacts=self.artifacts)
        base = self.state / "sdk" / sdk.cache_key()
        prior = list(base.glob("attempt-*"))
        self.assertEqual(len(prior), 1)
        self.assertIsNone(sdk.cached_sdk(self.state))
        site = self.install()
        self.assertNotEqual(site.parent, prior[0])
        self.assertTrue(prior[0].is_dir())
        self.assertEqual(len(list(base.glob("attempt-*"))), 2)

    def test_mismatched_metadata_or_startup_hook_fails_without_receipt(self) -> None:
        site = self.install()
        metadata = next(site.glob("openai-*.dist-info")) / "METADATA"
        metadata.write_text("Name: openai\nVersion: 999\n")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "distribution_set_mismatch"):
            sdk.verify_metadata(site)
        hook = site / "startup.pth"
        hook.write_text("import untrusted_hook\n")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "sdk_member_rejected"):
            sdk.inventory(site)

    def test_launcher_sdk_consent_is_distinct_from_application_or_runtime_consent(self) -> None:
        for flags in ([], ["--allow-install"], ["--allow-runtime-install"]):
            with self.subTest(flags=flags), redirect_stdout(io.StringIO()), patch.object(sdk, "install_sdk") as install:
                self.assertEqual(bootstrap.main(["--state-dir", str(self.state), *flags, "setup-sdk"]), 2)
                install.assert_not_called()
                self.assertFalse(self.state.exists())

    def test_receipt_cannot_redirect_to_another_attempt_or_outside_state(self) -> None:
        site = self.install()
        receipt = site.parent.parent / "active.json"
        document = json.loads(receipt.read_bytes())
        document["attempt"] = "../outside"
        receipt.write_text(json.dumps(document))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "sdk_receipt_invalid"):
            sdk.cached_sdk(self.state)

    def test_only_locked_certifi_public_ca_package_data_is_allowed_as_pem(self) -> None:
        site = self.install()
        package = site / "certifi"
        package.mkdir(mode=0o700)
        public_bundle = package / "cacert.pem"
        public_bundle.write_text("synthetic public package data; no certificate or secret\n")
        public_bundle.chmod(0o600)
        self.assertIn("certifi/cacert.pem", sdk.inventory(site))
        unexpected = package / "unexpected.pem"
        unexpected.write_text("synthetic rejected name\n")
        unexpected.chmod(0o600)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "sdk_member_rejected"):
            sdk.inventory(site)

    def test_timeout_terminates_only_the_new_setup_process_group(self) -> None:
        process = MagicMock()
        process.pid = 12345
        process.wait.side_effect = [subprocess.TimeoutExpired(["synthetic-tool"], 1), 0]
        with patch("tools.bootstrap_sdk.subprocess.Popen", return_value=process) as spawn, \
                patch("tools.bootstrap_sdk.os.killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                sdk.run_bounded(["synthetic-tool"], self.root, {"PATH": "/usr/bin:/bin"}, timeout=1)
        kill.assert_called_once()
        assert kill.call_args is not None
        assert spawn.call_args is not None
        self.assertEqual(kill.call_args.args[0], 12345)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["umask"], 0o077)
        self.assertEqual(spawn.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin"})


if __name__ == "__main__":
    unittest.main()
