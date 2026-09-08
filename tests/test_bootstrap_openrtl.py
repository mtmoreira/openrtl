"""Synthetic pure-wheel bootstrap checks; no installs, network or model qualification."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request
import warnings
import zipfile

from tools import bootstrap_openrtl as bootstrap
from openrtl.onboarding import SetupError


FILENAME = "agentrig-0.3.0-py3-none-any.whl"
ORIGIN = "https://github.com/mtmoreira/agentrig/releases/download/v0.3.0/" + FILENAME
METADATA_PATH = "agentrig-0.3.0.dist-info/METADATA"
WHEEL_PATH = "agentrig-0.3.0.dist-info/WHEEL"


def wheel_bytes(*, extra: tuple[tuple[str, bytes, int], ...] = (),
                metadata: bytes | None = None, wheel: bytes | None = None,
                initializer: bytes = b'raise RuntimeError("bootstrap must not import this fixture")\n') -> bytes:
    """Tiny local bytes with a matching local pin, never the published package."""
    entries = (
        ("agentrig/__init__.py", initializer, stat.S_IFREG | 0o644),
        (METADATA_PATH, metadata if metadata is not None else
         b"Metadata-Version: 2.1\nName: agentrig\nVersion: 0.3.0\n\n", stat.S_IFREG | 0o644),
        (WHEEL_PATH, wheel if wheel is not None else
         b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n", stat.S_IFREG | 0o644),
        ("agentrig-0.3.0.dist-info/RECORD", b"", stat.S_IFREG | 0o644),
        *extra,
    )
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, payload, mode in entries:
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, payload)
    return stream.getvalue()


def wheel_pin(data: bytes) -> bootstrap.WheelPin:
    return bootstrap.WheelPin(FILENAME, hashlib.sha256(data).hexdigest(), len(data), ORIGIN)


class BootstrapWheelValidationTest(unittest.TestCase):
    def test_valid_pure_wheel_is_checked_without_executing_package_code(self) -> None:
        data = wheel_bytes()
        bootstrap.verify_wheel(data, wheel_pin(data))

    def test_safe_package_and_metadata_directories_are_compatible_with_real_wheels(self) -> None:
        data = wheel_bytes(extra=(
            ("agentrig/", b"", stat.S_IFDIR | 0o755),
            ("agentrig-0.3.0.dist-info/", b"", stat.S_IFDIR | 0o755),
            ("agentrig-0.3.0.dist-info/licenses/", b"", stat.S_IFDIR | 0o755),
            ("agentrig/helpers/", b"", stat.S_IFDIR | 0o755),
            ("agentrig/helpers/local.py", b"VALUE = 1\n", stat.S_IFREG | 0o644),
        ))
        bootstrap.verify_wheel(data, wheel_pin(data))

    def test_directory_links_traversal_duplicate_and_normalized_file_conflicts_are_rejected(self) -> None:
        for extra in (
            (("agentrig/link/", b"", stat.S_IFLNK | 0o777),),
            (("agentrig/../outside/", b"", stat.S_IFDIR | 0o755),),
            (("/agentrig/absolute/", b"", stat.S_IFDIR | 0o755),),
            (("agentrig/double//", b"", stat.S_IFDIR | 0o755),),
            (("agentrig/wrong-kind/", b"", stat.S_IFREG | 0o644),),
            (("agentrig/payload/", b"directory payload", stat.S_IFDIR | 0o755),),
            (("agentrig/repeated/", b"", stat.S_IFDIR | 0o755),
             ("agentrig/repeated/", b"", stat.S_IFDIR | 0o755)),
            (("agentrig/conflict", b"file payload", stat.S_IFREG | 0o644),
             ("agentrig/conflict/", b"", stat.S_IFDIR | 0o755)),
            (("agentrig/conflict/", b"", stat.S_IFDIR | 0o755),
             ("agentrig/conflict", b"file payload", stat.S_IFREG | 0o644)),
        ):
            with self.subTest(extra=extra):
                data = wheel_bytes(extra=extra)
                with self.assertRaisesRegex(bootstrap.BootstrapError, "wheel_member_rejected"):
                    bootstrap.verify_wheel(data, wheel_pin(data))

    def test_hash_and_size_mismatch_fail_before_zip_processing(self) -> None:
        data = wheel_bytes()
        for pin in (replace(wheel_pin(data), sha256="0" * 64), replace(wheel_pin(data), size_bytes=len(data) + 1)):
            with self.subTest(pin=pin), patch("tools.bootstrap_openrtl.zipfile.ZipFile") as archive:
                with self.assertRaisesRegex(bootstrap.BootstrapError, "hash_or_size_mismatch"):
                    bootstrap.verify_wheel(data, pin)
                archive.assert_not_called()

    def test_traversal_native_symlink_duplicate_and_install_hook_members_are_rejected(self) -> None:
        rejected = (
            ("../outside.py", stat.S_IFREG | 0o644),
            ("/agentrig/absolute.py", stat.S_IFREG | 0o644),
            ("agentrig/../outside.py", stat.S_IFREG | 0o644),
            ("agentrig//empty.py", stat.S_IFREG | 0o644),
            ("agentrig/./dot.py", stat.S_IFREG | 0o644),
            ("agentrig\\escaped.py", stat.S_IFREG | 0o644),
            ("agentrig/native.so", stat.S_IFREG | 0o644),
            ("agentrig/native.dylib", stat.S_IFREG | 0o644),
            ("agentrig/native.dll", stat.S_IFREG | 0o644),
            ("agentrig/native.pyd", stat.S_IFREG | 0o644),
            ("agentrig/startup.pth", stat.S_IFREG | 0o644),
            ("agentrig-0.3.0.data/scripts/install", stat.S_IFREG | 0o755),
            ("agentrig/linked.py", stat.S_IFLNK | 0o777),
            ("agentrig/pipe.py", stat.S_IFIFO | 0o600),
            ("agentrig/subdirectory/", stat.S_IFDIR | 0o755),
            ("agentrig/__init__.py", stat.S_IFREG | 0o644),
        )
        for name, mode in rejected:
            with self.subTest(name=name):
                data = wheel_bytes(extra=((name, b"untrusted synthetic member", mode),))
                with self.assertRaisesRegex(bootstrap.BootstrapError, "wheel_member_rejected"):
                    bootstrap.verify_wheel(data, wheel_pin(data))

    def test_identity_unconditional_dependency_and_nonpure_wheel_fail_closed(self) -> None:
        for metadata, wheel, expected in (
            (b"Name: different-package\nVersion: 0.3.0\n", None, "identity_mismatch"),
            (b"Name: agentrig\nVersion: 0.3.1\n", None, "identity_mismatch"),
            (b"Name: agentrig\nVersion: 0.3.0\nRequires-Dist: unapproved-package\n", None, "unexpected_base_dependency"),
            (None, b"Root-Is-Purelib: false\nTag: cp312-macosx_arm64\n", "pure_python_wheel_required"),
        ):
            with self.subTest(expected=expected):
                data = wheel_bytes(metadata=metadata, wheel=wheel)
                with self.assertRaisesRegex(bootstrap.BootstrapError, expected):
                    bootstrap.verify_wheel(data, wheel_pin(data))

    def test_expansion_limit_is_checked_before_member_decompression(self) -> None:
        data = wheel_bytes(extra=(("agentrig/large.py", b"x" * 4096, stat.S_IFREG | 0o644),))
        with patch("tools.bootstrap_openrtl.MAX_EXPANDED_BYTES", 512):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "wheel_expansion_limit"):
                bootstrap.verify_wheel(data, wheel_pin(data))


class BootstrapLocalStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "private product state"
        self.data = wheel_bytes()
        self.pin = wheel_pin(self.data)

    def wheelhouse(self, payload: bytes | None = None) -> Path:
        selected = self.root / "offline wheelhouse"
        selected.mkdir(mode=0o700)
        (selected / FILENAME).write_bytes(self.data if payload is None else payload)
        (selected / FILENAME).chmod(0o644)
        return selected

    def test_denial_precedes_all_writes_and_fetching(self) -> None:
        fetch = MagicMock(side_effect=AssertionError("must not fetch"))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "consent_required"):
            bootstrap.install_wheel(self.state, self.pin, authorized=False, offline=False, fetch=fetch)
        self.assertFalse(self.state.exists())
        fetch.assert_not_called()

    def test_offline_without_available_wheel_fails_before_writes_and_never_fetches(self) -> None:
        fetch = MagicMock(side_effect=AssertionError("must not fetch"))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "offline_wheel_unavailable"):
            bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=True, fetch=fetch)
        fetch.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_local_wheel_setup_is_private_resumable_and_preserves_unrelated_files(self) -> None:
        self.state.mkdir(mode=0o700)
        retained = self.state / "retained-evidence.txt"
        retained.write_text("unrelated retained evidence")
        fetch = MagicMock(side_effect=AssertionError("must not fetch"))
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=True,
                                            wheelhouse=self.wheelhouse(), fetch=fetch)
        self.assertEqual(installed.read_bytes(), self.data)
        self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(installed.parent.stat().st_mode), 0o700)
        self.assertEqual(bootstrap.cached_wheel(self.state, self.pin), installed)
        self.assertEqual(bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=True,
                                               fetch=fetch), installed)
        self.assertEqual(retained.read_text(), "unrelated retained evidence")
        fetch.assert_not_called()
        self.assertEqual(sorted(path.name for path in installed.parent.iterdir()), [".install.lock", FILENAME])

    def test_interrupted_download_and_atomic_write_can_be_retried_without_partial_wheel(self) -> None:
        interrupted = MagicMock(side_effect=OSError("synthetic interrupted fetch"))
        with self.assertRaises(OSError):
            bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False, fetch=interrupted)
        self.assertIsNone(bootstrap.cached_wheel(self.state, self.pin))
        destination = self.state / "dependencies" / self.pin.sha256
        retained = destination / "retained-note.txt"
        retained.write_text("preserve me")
        with patch("tools.bootstrap_openrtl.os.rename", side_effect=OSError("synthetic interrupted publication")):
            with self.assertRaises(OSError):
                bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                        fetch=lambda pin: self.data)
        self.assertIsNone(bootstrap.cached_wheel(self.state, self.pin))
        self.assertFalse(any(path.name.startswith(".download-") for path in destination.iterdir()))
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        self.assertEqual(installed.read_bytes(), self.data)
        self.assertEqual(retained.read_text(), "preserve me")

    def test_invalid_offline_wheel_is_not_cached_or_executed(self) -> None:
        damaged = bytes([self.data[0] ^ 1]) + self.data[1:]
        with self.assertRaisesRegex(bootstrap.BootstrapError, "hash_or_size_mismatch"):
            bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=True,
                                    wheelhouse=self.wheelhouse(damaged))
        self.assertIsNone(bootstrap.cached_wheel(self.state, self.pin))

    def test_tampered_cache_is_refused_without_replacement_or_fetch(self) -> None:
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        damaged = bytes([self.data[0] ^ 1]) + self.data[1:]
        installed.write_bytes(damaged)
        fetch = MagicMock(side_effect=AssertionError("must not replace tampered cache"))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "hash_or_size_mismatch"):
            bootstrap.cached_wheel(self.state, self.pin)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "hash_or_size_mismatch"):
            bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False, fetch=fetch)
        self.assertEqual(installed.read_bytes(), damaged)
        fetch.assert_not_called()

    def test_wheelhouse_symlink_hardlink_and_named_pipe_are_rejected(self) -> None:
        selected = self.wheelhouse()
        target = selected / FILENAME
        retained = self.root / "retained-wheel.whl"
        target.rename(retained)
        target.symlink_to(retained)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.read_regular(target, self.pin.size_bytes)
        target.unlink()
        os.link(retained, target)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.read_regular(target, self.pin.size_bytes)
        target.unlink()
        os.mkfifo(target, 0o600)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.read_regular(target, self.pin.size_bytes)
        self.assertEqual(retained.read_bytes(), self.data)

    def test_insecure_state_or_intermediate_directory_is_rejected_before_fetch_or_writes(self) -> None:
        for selected in (self.state, self.state / "dependencies"):
            with self.subTest(directory=selected.name):
                self.state.mkdir(mode=0o700, exist_ok=True)
                selected.mkdir(mode=0o700, exist_ok=True)
                retained = selected / "retained-note.txt"
                retained.write_text("preserve existing files")
                selected.chmod(0o755)
                before = sorted(str(path.relative_to(self.state)) for path in self.state.rglob("*"))
                fetch = MagicMock(side_effect=AssertionError("insecure storage must not fetch"))
                try:
                    with self.assertRaises(SetupError):
                        bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False, fetch=fetch)
                    self.assertEqual(stat.S_IMODE(selected.stat().st_mode), 0o755)
                    self.assertEqual(retained.read_text(), "preserve existing files")
                    self.assertEqual(sorted(str(path.relative_to(self.state)) for path in self.state.rglob("*")), before)
                    fetch.assert_not_called()
                finally:
                    selected.chmod(0o700)

    def test_cache_root_intermediates_and_wheel_each_require_private_permissions(self) -> None:
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        for selected in (self.state, self.state / "dependencies", installed.parent, installed):
            with self.subTest(selected=selected.name):
                expected_mode = 0o600 if selected.is_file() else 0o700
                selected.chmod(0o644 if selected.is_file() else 0o755)
                fetch = MagicMock(side_effect=AssertionError("private cache failure must not fetch"))
                try:
                    with self.assertRaises((SetupError, bootstrap.BootstrapError)):
                        bootstrap.cached_wheel(self.state, self.pin)
                    with self.assertRaises((SetupError, bootstrap.BootstrapError)):
                        bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False, fetch=fetch)
                    fetch.assert_not_called()
                    self.assertEqual(installed.read_bytes(), self.data)
                finally:
                    selected.chmod(expected_mode)

    def test_wrong_owner_directory_and_private_wheel_fail_before_fetch(self) -> None:
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        other_uid = os.getuid() + 1
        fetch = MagicMock(side_effect=AssertionError("unowned state must not fetch"))
        with patch("tools.bootstrap_openrtl.os.getuid", return_value=other_uid):
            with self.assertRaisesRegex(SetupError, "private_owned"):
                bootstrap.cached_wheel(self.state, self.pin)
            with self.assertRaises(SetupError):
                bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False, fetch=fetch)
            with self.assertRaisesRegex(bootstrap.BootstrapError, "private_cache_file_required"):
                bootstrap.read_regular(installed, self.pin.size_bytes, private=True)
        fetch.assert_not_called()
        self.assertEqual(installed.read_bytes(), self.data)

    def test_main_batch_denial_and_offline_default_never_prompt_or_write(self) -> None:
        for options in ([], ["--offline"]):
            with self.subTest(options=options), \
                    patch.object(sys, "path", list(sys.path)), \
                    patch("tools.bootstrap_openrtl.load_pin", return_value=self.pin), \
                    patch("tools.bootstrap_openrtl.install_wheel", side_effect=AssertionError("must not install")), \
                    patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                    patch("sys.stdin.isatty", return_value=True), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bootstrap.main(["--state-dir", str(self.state), *options,
                                                 "batch", "--project", "project with spaces"]), 2)
            self.assertIn("without installation", output.getvalue())
            self.assertFalse(self.state.exists())

    def test_main_interactive_denial_has_no_persistent_effect(self) -> None:
        with redirect_stdout(io.StringIO()), patch.object(sys, "path", list(sys.path)), \
                patch("tools.bootstrap_openrtl.load_pin", return_value=self.pin), \
                patch("tools.bootstrap_openrtl.install_wheel", side_effect=AssertionError("must not install")), \
                patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", return_value="n") as prompt:
            self.assertEqual(bootstrap.main(["--state-dir", str(self.state)]), 2)
        prompt.assert_called_once()
        self.assertFalse(self.state.exists())

    def test_main_explicit_offline_install_and_cache_rerun_forward_exact_command(self) -> None:
        wheelhouse = self.wheelhouse()
        command = ["batch", "--project", "project with spaces", "--max-calls", "3"]
        with patch.object(sys, "path", list(sys.path)), \
                patch("tools.bootstrap_openrtl.load_pin", return_value=self.pin), \
                patch("openrtl.onboarding.main", return_value=7) as frontend, \
                patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(bootstrap.main(["--state-dir", str(self.state), "--allow-install", "--offline",
                                             "--wheelhouse", str(wheelhouse), *command]), 7)
            frontend.assert_called_once_with(["--state-dir", str(self.state), *command])
            frontend.reset_mock()
            with patch("tools.bootstrap_openrtl.install_wheel", side_effect=AssertionError("cached bytes require no new installation")):
                self.assertEqual(bootstrap.main(["--state-dir", str(self.state), *command]), 7)
            frontend.assert_called_once_with(["--state-dir", str(self.state), *command])

    def test_main_refuses_tampered_cache_before_frontend_or_installation(self) -> None:
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        damaged = bytes([self.data[0] ^ 1]) + self.data[1:]
        installed.write_bytes(damaged)
        with patch.object(sys, "path", list(sys.path)), \
                patch("tools.bootstrap_openrtl.load_pin", return_value=self.pin), \
                patch("openrtl.onboarding.main", side_effect=AssertionError("must not load tampered code")), \
                patch("tools.bootstrap_openrtl.install_wheel", side_effect=AssertionError("must not replace evidence")), \
                patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(bootstrap.main(["--state-dir", str(self.state), "--allow-install", "batch"]), 2)
        self.assertEqual(installed.read_bytes(), damaged)

    def test_doctor_sees_verified_cached_wheel_metadata_without_installing_or_importing_it(self) -> None:
        installed = bootstrap.install_wheel(self.state, self.pin, authorized=True, offline=False,
                                            fetch=lambda pin: self.data)
        before = {str(path.relative_to(self.state)): path.read_bytes()
                  for path in self.state.rglob("*") if path.is_file()}
        with patch.object(sys, "path", list(sys.path)), \
                patch("tools.bootstrap_openrtl.load_pin", return_value=self.pin), \
                patch("tools.bootstrap_openrtl.install_wheel", side_effect=AssertionError("doctor must not install")), \
                patch("builtins.input", side_effect=AssertionError("doctor must not prompt")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bootstrap.main(["--state-dir", str(self.state), "doctor", "--json", "--require-local"]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["local_review_ready"])
        self.assertEqual(report["packages"]["agentrig"], "0.3.0")
        self.assertFalse(report["provider_authorized"])
        self.assertFalse(report["credential_resolution"])
        self.assertFalse(report["simulation_performed"])
        self.assertEqual(installed.read_bytes(), self.data)
        self.assertEqual({str(path.relative_to(self.state)): path.read_bytes()
                          for path in self.state.rglob("*") if path.is_file()}, before)

    def test_pin_parser_requires_fixed_origin_name_hash_and_size(self) -> None:
        document: dict[str, object] = {
            "schema": "openrtl.bootstrap-dependencies.v1",
            "agentrig": {"filename": self.pin.filename, "sha256": self.pin.sha256,
                         "size_bytes": self.pin.size_bytes, "url": self.pin.url},
            "provenance": "synthetic unit-test bytes only",
        }
        lock = self.root / "dependencies.json"
        lock.write_text(json.dumps(document))
        self.assertEqual(bootstrap.load_pin(lock), self.pin)
        for key, value in (("url", "https://example.invalid/package.whl"), ("filename", "../package.whl"),
                           ("sha256", "invalid"), ("size_bytes", True), ("size_bytes", 0)):
            with self.subTest(key=key, value=value):
                changed = {"filename": self.pin.filename, "sha256": self.pin.sha256,
                           "size_bytes": self.pin.size_bytes, "url": self.pin.url, key: value}
                lock.write_text(json.dumps({**document, "agentrig": changed}))
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.load_pin(lock)


class BootstrapDownloadPolicyTest(unittest.TestCase):
    def test_child_classifies_failures_without_emitting_untrusted_values(self) -> None:
        marker = "synthetic-untrusted-remote-response"
        failures: list[tuple[Exception, int]] = [
            (HTTPError(ORIGIN, status, marker, Message(), None), code)
            for status, code in ((404, 20), (410, 20), (401, 21), (403, 21),
                                 (429, 22), (500, 23))
        ]
        for error, code in ((ssl.SSLCertVerificationError(marker), 24),
                            (socket.gaierror(marker), 25), (TimeoutError(marker), 26),
                            (ConnectionRefusedError(marker), 27)):
            failures.extend(((error, code), (URLError(error), code)))
        failures.extend(((URLError(marker), 27), (RuntimeError(marker), 2),
                         (bootstrap.BootstrapError("download_redirect_rejected"), 29),
                         (bootstrap.BootstrapError("wheel_hash_or_size_mismatch"), 28)))
        for failure, expected in failures:
            with self.subTest(error=type(failure), expected=expected), \
                    patch("tools.bootstrap_openrtl._download_bytes", side_effect=failure), \
                    patch("tools.bootstrap_openrtl.verify_wheel") as verify, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bootstrap.fetch_wheel_with_consent(), expected)
                self.assertEqual(output.getvalue(), "")
                verify.assert_not_called()

    def test_child_returns_only_verified_wheel_bytes_on_success(self) -> None:
        data = wheel_bytes()
        output = MagicMock()
        with patch("tools.bootstrap_openrtl.load_pin", return_value=wheel_pin(data)), \
                patch("tools.bootstrap_openrtl._download_bytes", return_value=data), \
                patch("tools.bootstrap_openrtl.sys.stdout", output):
            self.assertEqual(bootstrap.fetch_wheel_with_consent(), 0)
        output.buffer.write.assert_called_once_with(data)
        output.reset_mock()
        with patch("tools.bootstrap_openrtl._download_bytes", return_value=data), \
                patch("tools.bootstrap_openrtl.sys.stdout", output):
            self.assertEqual(bootstrap.fetch_wheel_with_consent(), 28)
        output.buffer.write.assert_not_called()

    def test_parent_maps_child_codes_and_frontend_shows_safe_recovery_hints(self) -> None:
        for exit_code, (code, hint) in bootstrap.DOWNLOAD_FAILURES.items():
            completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
                args=[], returncode=exit_code, stdout=b"synthetic-untrusted-response")
            with self.subTest(code=code), \
                    patch("tools.bootstrap_openrtl.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "^" + code + "$"):
                    bootstrap.download(bootstrap.load_pin())
            with patch("tools.bootstrap_sdk.cached_sdk", return_value=None), \
                    patch("tools.bootstrap_openrtl.load_pin", side_effect=bootstrap.BootstrapError(code)), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bootstrap.main([]), 2)
            self.assertIn(hint, output.getvalue())
            self.assertNotIn("synthetic-untrusted-response", output.getvalue())

    def test_download_uses_no_proxy_or_authentication_and_bounds_bytes(self) -> None:
        data = wheel_bytes()
        pin = wheel_pin(data)
        response = MagicMock()
        response.url = ORIGIN
        response.read1.side_effect = [data, b""]
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch("tools.bootstrap_openrtl.build_opener", return_value=opener) as build:
            self.assertEqual(bootstrap._download_bytes(pin), data)
        assert build.call_args is not None
        proxy = next(handler for handler in build.call_args.args if isinstance(handler, ProxyHandler))
        self.assertEqual(getattr(proxy, "proxies"), {})
        assert opener.open.call_args is not None
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ORIGIN)
        self.assertFalse(any(name.lower() in ("authorization", "cookie", "proxy-authorization")
                             for name, value in request.header_items()))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 20)
        response.read1.assert_any_call(min(65536, pin.size_bytes + 1))
        response.read1.side_effect = [data + b"x"]
        with patch("tools.bootstrap_openrtl.build_opener", return_value=opener):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "download_limit"):
                bootstrap._download_bytes(pin)

    def test_download_deadline_and_untrusted_redirect_fail_without_real_network(self) -> None:
        data = wheel_bytes()
        response = MagicMock()
        response.url = ORIGIN
        response.read1.return_value = data
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch("tools.bootstrap_openrtl.build_opener", return_value=opener), \
                patch("tools.bootstrap_openrtl.time.monotonic", side_effect=[0.0, 61.0]):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "download_limit"):
                bootstrap._download_bytes(wheel_pin(data))
        for url in ("http://github.com/release", "https://example.invalid/release", "https://user:synthetic@github.com/release"):
            with self.subTest(url=url), self.assertRaisesRegex(bootstrap.BootstrapError, "redirect_rejected"):
                bootstrap.RestrictedRedirect().redirect_request(Request(ORIGIN), None, 302, "redirect", {}, url)

    def test_parent_uses_exact_isolated_child_argv_minimal_environment_and_deadline(self) -> None:
        data = wheel_bytes()
        pin = wheel_pin(data)
        completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(args=[], returncode=0, stdout=data)
        with patch("tools.bootstrap_openrtl.load_pin", return_value=pin), \
                patch("tools.bootstrap_openrtl.subprocess.run", return_value=completed) as run, \
                patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-never-forward", "HTTPS_PROXY": "synthetic-never-forward"}):
            self.assertEqual(bootstrap.download(pin), data)
        run.assert_called_once_with(
            [sys.executable, "-I", "-S", str(bootstrap.ROOT / "tools/bootstrap_openrtl.py"),
             "--fetch-wheel-with-consent"],
            cwd=bootstrap.ROOT, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, check=False,
        )

    def test_parent_timeout_and_child_failure_expose_only_standard_diagnostics(self) -> None:
        data = wheel_bytes()
        pin = wheel_pin(data)
        with patch("tools.bootstrap_openrtl.load_pin", return_value=pin), \
                patch("tools.bootstrap_openrtl.subprocess.run", side_effect=subprocess.TimeoutExpired(
                    ["synthetic-child"], 60, output=b"synthetic-untrusted-response")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "^download_deadline_exceeded$"):
                bootstrap.download(pin)
        completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(args=[], returncode=17, stdout=b"synthetic-untrusted-response")
        with patch("tools.bootstrap_openrtl.load_pin", return_value=pin), \
                patch("tools.bootstrap_openrtl.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "^public_wheel_download_unavailable$"):
                bootstrap.download(pin)

    def test_parent_rejects_unchecked_pin_and_reverifies_successful_child_bytes(self) -> None:
        data = wheel_bytes()
        pin = wheel_pin(data)
        with patch("tools.bootstrap_openrtl.load_pin", return_value=replace(pin, sha256="0" * 64)), \
                patch("tools.bootstrap_openrtl.subprocess.run", side_effect=AssertionError("mismatched pin must not spawn")) as run:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "download_requires_checked_in_pin"):
                bootstrap.download(pin)
            run.assert_not_called()
        damaged = bytes([data[0] ^ 1]) + data[1:]
        completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(args=[], returncode=0, stdout=damaged)
        with patch("tools.bootstrap_openrtl.load_pin", return_value=pin), \
                patch("tools.bootstrap_openrtl.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "hash_or_size_mismatch"):
                bootstrap.download(pin)


class BootstrapShellLauncherTest(unittest.TestCase):
    def test_launcher_handles_spaces_and_forwards_exact_argv_with_explicit_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "clone with spaces"
            root.mkdir()
            launcher = root / "openrtl"
            shutil.copyfile(bootstrap.ROOT / "openrtl", launcher)
            interpreter = root / "stub interpreter"
            interpreter.write_text(
                '#!/bin/sh\n'
                'printf "%s\\0" "$@" >> "$OPENRTL_STUB_ARGV"\n'
                'printf "%s\\0" __CALL_END__ >> "$OPENRTL_STUB_ARGV"\n'
                'if [ "$3" = "-c" ]; then exit "${OPENRTL_STUB_PROBE_STATUS:-0}"; fi\n'
                'exit 7\n')
            interpreter.chmod(0o700)
            recorded = root / "arguments.bin"
            environment = {"PATH": "/usr/bin:/bin", "OPENRTL_PYTHON": str(interpreter),
                           "OPENRTL_STUB_ARGV": str(recorded)}
            arguments = ["batch", "--project", "path with spaces", "--model", "literal$(unchanged)"]
            result = subprocess.run(["/bin/sh", str(launcher), *arguments], env=environment,
                                    capture_output=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 7, result.stderr.decode())
            recorded_args = recorded.read_bytes().decode().split("\x00")
            boundary = recorded_args.index("__CALL_END__")
            self.assertEqual(recorded_args[:3], ["-I", "-S", "-c"])
            self.assertEqual(recorded_args[boundary + 1:], ["-I", "-S", "-B", str(root / "tools" / "bootstrap_openrtl.py"),
                                                           *arguments, "__CALL_END__", ""])
            recorded.unlink()
            environment["OPENRTL_STUB_PROBE_STATUS"] = "2"
            result = subprocess.run(["/bin/sh", str(launcher), *arguments], env=environment,
                                    capture_output=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("No replacement was installed", result.stderr.decode())
            self.assertEqual(recorded.read_bytes().decode().split("\x00").count("__CALL_END__"), 1)


if __name__ == "__main__":
    unittest.main()
