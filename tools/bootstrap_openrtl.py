"""Consent-gated, pinned pure-wheel bootstrap; no pip, build hooks or sibling source.

The cloned OpenRTL source remains the selected application. Runtime and optional
SDK setup are separate consent gates; clean-user qualification remains pending.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.parser import BytesParser
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "bootstrap" / "dependencies.json"
MAX_WHEEL_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024

# Child failures cross the process boundary as fixed exit codes, never remote
# response bodies, URLs, headers or exception messages.
DOWNLOAD_FAILURES = {
    20: ("public_wheel_not_found", "The pinned public artifact returned HTTP 404/410. Report the missing release artifact; an approved offline wheelhouse can supply the same pinned bytes."),
    21: ("public_wheel_access_denied", "The public artifact refused anonymous access. Do not supply credentials; report the public release availability problem."),
    22: ("public_wheel_rate_limited", "The public host rate-limited this request. Wait before retrying the same setup command."),
    23: ("public_wheel_http_error", "The public host returned an HTTP error. Retry later or use an approved offline wheelhouse."),
    24: ("public_wheel_tls_failed", "Python could not establish verified TLS. Check the selected interpreter's trusted certificate setup or use an approved offline wheelhouse; keep TLS verification enabled."),
    25: ("public_wheel_dns_failed", "Python could not resolve the public host. Check network/DNS access, then retry the same setup command."),
    26: ("download_deadline_exceeded", "The download timed out. Check connectivity, then retry the same setup command."),
    27: ("public_wheel_connection_failed", "The public connection failed. Check connectivity or use an approved offline wheelhouse."),
    28: ("public_wheel_verification_failed", "The downloaded artifact failed the pinned integrity or archive checks. Preserve the pin and report the release artifact mismatch."),
    29: ("public_wheel_redirect_rejected", "The download left the allowed HTTPS release hosts. Report the release redirect; do not widen trust automatically."),
}


class BootstrapError(ValueError):
    """Contains only an allowlisted diagnostic, never remote output or paths."""


@dataclass(frozen=True)
class WheelPin:
    filename: str
    sha256: str
    size_bytes: int
    url: str


def require(condition: bool, code: str) -> None:
    if not condition:
        raise BootstrapError(code)


def load_pin(filename: Path = LOCK) -> WheelPin:
    require(not filename.is_symlink() and filename.is_file() and filename.stat().st_size <= 8192,
            "dependency_lock_unavailable")
    document = json.loads(filename.read_bytes())
    require(type(document) is dict and set(document) == {"schema", "agentrig", "provenance"}
            and document["schema"] == "openrtl.bootstrap-dependencies.v1", "dependency_lock_invalid")
    row = document["agentrig"]
    require(type(row) is dict and set(row) == {"filename", "sha256", "size_bytes", "url"}, "dependency_pin_invalid")
    pin = WheelPin(**row)
    require(pin.filename == "agentrig-0.3.1.dev12-py3-none-any.whl" and
            type(pin.sha256) is str and re.fullmatch(r"[a-f0-9]{64}", pin.sha256) is not None and
            type(pin.size_bytes) is int and 0 < pin.size_bytes <= MAX_WHEEL_BYTES,
            "dependency_pin_invalid")
    require(pin.url == "https://github.com/mtmoreira/agentrig/releases/download/v0.3.1.dev12/" + pin.filename,
            "dependency_origin_invalid")
    return pin


def verify_wheel(data: bytes, pin: WheelPin) -> None:
    require(len(data) == pin.size_bytes and hashlib.sha256(data).hexdigest() == pin.sha256,
            "dependency_hash_or_size_mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= 4096, "wheel_manifest_invalid")
        names: set[str] = set()
        total = 0
        for item in entries:
            normalized = item.filename[:-1] if item.is_dir() else item.filename
            relative = PurePosixPath(normalized)
            mode = item.external_attr >> 16
            valid_kind = (stat.S_IFMT(mode) in (0, stat.S_IFDIR) and item.file_size == 0
                          if item.is_dir() else stat.S_IFMT(mode) in (0, stat.S_IFREG))
            require(not relative.is_absolute() and
                    all(part not in ("", ".", "..") for part in normalized.split("/")) and
                    "\\" not in item.filename and "\x00" not in item.filename and
                    relative.parts[0] in ("agentrig", "agentrig-0.3.1.dev12.dist-info") and
                    not any(part.startswith(".") for part in relative.parts) and
                    not item.filename.endswith((".pth", ".so", ".dylib", ".dll", ".pyd", ".key", ".pem", ".p12", ".pfx")) and
                    valid_kind and normalized not in names,
                    "wheel_member_rejected")
            names.add(normalized)
            total += item.file_size
            require(0 <= item.file_size <= MAX_WHEEL_BYTES and total <= MAX_EXPANDED_BYTES,
                    "wheel_expansion_limit")
        metadata = BytesParser().parsebytes(archive.read("agentrig-0.3.1.dev12.dist-info/METADATA"))
        require(metadata["Name"] == "agentrig" and metadata["Version"] == "0.3.1.dev12",
                "wheel_identity_mismatch")
        require(all("extra ==" in requirement for requirement in metadata.get_all("Requires-Dist", [])),
                "unexpected_base_dependency")
        wheel = archive.read("agentrig-0.3.1.dev12.dist-info/WHEEL")
        require(b"Root-Is-Purelib: true" in wheel and b"Tag: py3-none-any" in wheel,
                "pure_python_wheel_required")
        require("agentrig/__init__.py" in names, "wheel_package_missing")


def read_regular(filename: Path, bound: int, *, private: bool = False) -> bytes:
    selected = filename.absolute()
    require(".." not in selected.parts and not any(p.is_symlink() for p in (*selected.parents, selected)),
            "wheelhouse_path_rejected")
    descriptor = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= bound,
                "wheel_file_rejected")
        if private:
            require(info.st_uid == os.getuid() and not info.st_mode & 0o077, "private_cache_file_required")
        data = stream.read(bound + 1)
    require(len(data) <= bound, "wheel_size_limit")
    return data


def _trusted_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname in
            {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
            and parsed.username is None and parsed.password is None and parsed.port in (None, 443))


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Request | None:
        require(_trusted_url(newurl), "download_redirect_rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_bytes(pin: WheelPin) -> bytes:
    # Disable proxy/environment authentication; use only HTTPS and no cookies.
    require(_trusted_url(pin.url), "download_origin_rejected")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), RestrictedRedirect())
    request = Request(pin.url, headers={"User-Agent": "OpenRTL-bootstrap/1", "Accept": "application/octet-stream"})
    started = time.monotonic()
    chunks: list[bytes] = []
    count = 0
    with opener.open(request, timeout=20) as response:
        require(_trusted_url(response.url), "download_origin_rejected")
        while chunk := response.read1(min(65536, pin.size_bytes + 1 - count)):
            count += len(chunk)
            require(count <= pin.size_bytes and time.monotonic() - started <= 60, "download_limit")
            chunks.append(chunk)
    return b"".join(chunks)


def download(pin: WheelPin) -> bytes:
    # A socket timeout alone is not a wall-clock deadline. Keep networking in
    # one dedicated, bounded child so a slow/blocked read can be terminated.
    require(pin == load_pin(), "download_requires_checked_in_pin")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(ROOT / "tools/bootstrap_openrtl.py"),
             "--fetch-wheel-with-consent"],
            cwd=ROOT, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        raise BootstrapError("download_deadline_exceeded") from None
    if completed.returncode != 0:
        raise BootstrapError(DOWNLOAD_FAILURES.get(
            completed.returncode, ("public_wheel_download_unavailable", ""))[0])
    verify_wheel(completed.stdout, pin)
    return completed.stdout


def fetch_wheel_with_consent() -> int:
    """Internal protocol for the checked-in public pin, with value-free errors."""
    try:
        pinned = load_pin()
        payload = _download_bytes(pinned)
        verify_wheel(payload, pinned)
        sys.stdout.buffer.write(payload)
    except HTTPError as error:
        if error.code in (404, 410):
            return 20
        if error.code in (401, 403):
            return 21
        return 22 if error.code == 429 else 23
    except (URLError, OSError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, ssl.SSLError):
            return 24
        if isinstance(reason, socket.gaierror):
            return 25
        return 26 if isinstance(reason, TimeoutError) else 27
    except BootstrapError as error:
        return 29 if str(error) in ("download_redirect_rejected", "download_origin_rejected") else 28
    except Exception:
        return 2
    return 0


def cached_wheel(state: Path, pin: WheelPin) -> Path | None:
    from openrtl.onboarding import _open_state
    for directory in (state, state / "dependencies", state / "dependencies" / pin.sha256):
        try:
            descriptor = _open_state(directory, create=False)
        except FileNotFoundError:
            return None
        os.close(descriptor)
    target = state / "dependencies" / pin.sha256 / pin.filename
    if not os.path.lexists(target):
        return None
    verify_wheel(read_regular(target, pin.size_bytes, private=True), pin)
    return target


def install_wheel(state: Path, pin: WheelPin, *, authorized: bool, offline: bool,
                  wheelhouse: Path | None = None, fetch: Callable[[WheelPin], bytes] = download) -> Path:
    require(authorized, "installation_consent_required")
    existing = cached_wheel(state, pin)
    if existing is not None:
        return existing
    require(wheelhouse is not None or not offline, "offline_wheel_unavailable")
    from openrtl.onboarding import _open_state
    destination = state / "dependencies" / pin.sha256
    for parent in (state, state / "dependencies"):
        descriptor = _open_state(parent, create=True)
        os.close(descriptor)
    directory = _open_state(destination, create=True)
    lock = None
    temporary = ".download-" + uuid.uuid4().hex
    created = False
    try:
        lock = os.open(".install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1,
                "installation_lock_rejected")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("another_setup_is_running") from None
        existing = cached_wheel(state, pin)
        if existing is not None:
            return existing
        data = read_regular(wheelhouse / pin.filename, pin.size_bytes) if wheelhouse is not None else fetch(pin)
        verify_wheel(data, pin)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require(not os.path.lexists(destination / pin.filename), "dependency_destination_changed")
        os.rename(temporary, pin.filename, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
        return destination / pin.filename
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        if lock is not None:
            os.close(lock)
        os.close(directory)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--allow-install", action="store_true")
    parser.add_argument("--allow-runtime-install", action="store_true")
    parser.add_argument("--runtime-artifacts", type=Path)
    parser.add_argument("--allow-sdk-install", action="store_true")
    parser.add_argument("--sdk-wheelhouse", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--state-dir", type=Path)
    options, forwarded = parser.parse_known_args(arguments)
    # The cloned source is explicit; never add a sibling checkout to sys.path.
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    sys.dont_write_bytecode = True
    from openrtl import onboarding
    from tools import bootstrap_sdk
    try:
        require(sys.version_info >= (3, 12), "python_3_12_required")
        state = options.state_dir or onboarding.default_state_dir()
        frontend = (["--state-dir", str(state)] if options.state_dir is not None else []) + forwarded
        if forwarded and forwarded[0] in ("--help", "-h"):
            print("Launcher options: --allow-install --allow-runtime-install --runtime-artifacts DIR "
                  "--offline --wheelhouse DIR --state-dir DIR; optional: setup-sdk --allow-sdk-install --sdk-wheelhouse DIR")
            return onboarding.main(frontend)
        if forwarded and forwarded[0] == "setup-sdk":
            require(forwarded == ["setup-sdk"], "sdk_setup_arguments_invalid")
            print("Optional setup uses uv 0.12.3 and the hashed OpenAI SDK 2.47.0 wheel lock in private OpenRTL state.")
            print("This permits SDK setup only. No credentials are read; no model or simulator is called.")
            if not options.allow_sdk_install:
                print("To approve SDK/tool preparation, rerun setup-sdk with --allow-sdk-install. "
                      "Offline setup also needs --sdk-wheelhouse DIR --runtime-artifacts DIR --offline.")
                return 2
            bootstrap_sdk.install_sdk(state, authorized=True, offline=options.offline,
                                      wheelhouse=options.sdk_wheelhouse, artifacts=options.runtime_artifacts)
            print("Optional SDK cache verified. Provider access and live qualification remain unverified.")
            return 0
        sdk = bootstrap_sdk.cached_sdk(state)
        if sdk is not None:
            sys.path.insert(0, str(sdk))
        pin = load_pin()
        selected = cached_wheel(state, pin)
        if selected is not None:
            sys.path.insert(0, str(selected))
        if forwarded and forwarded[0] in ("doctor", "setup"):
            # Diagnostics/configuration work before installation; a verified
            # cached pure wheel is visible to metadata-only readiness checks.
            return onboarding.main(frontend)
        if selected is None:
            consent = options.allow_install
            interactive = not forwarded and sys.stdin.isatty() and sys.stdout.isatty()
            print("OpenRTL needs the pinned AgentRig 0.3.1.dev12 application dependency (" + str(pin.size_bytes) + " bytes).")
            print("Setup keeps the verified wheel in private OpenRTL state. No package hooks, provider calls or simulation run during setup.")
            if not consent and interactive:
                effect = "Copy the selected local wheel" if options.wheelhouse else "Download the pinned public wheel from GitHub"
                consent = input(effect + " and prepare OpenRTL? [y/N] ").strip().casefold() == "y"
            if not consent:
                print("Setup stopped without installation. To approve this setup effect, rerun with --allow-install. Use --offline --wheelhouse DIR for an existing verified wheel.")
                return 2
            selected = install_wheel(state, pin, authorized=consent, offline=options.offline, wheelhouse=options.wheelhouse)
        # zipimport loads this pure wheel without extracting files or running
        # installation hooks. Every launch rehashes the exact pinned bytes.
        if str(selected) not in sys.path:
            sys.path.insert(0, str(selected))
        return onboarding.main(frontend)
    except (EOFError, KeyboardInterrupt):
        print("Setup interrupted. Verified cached dependencies remain reusable; execution permissions are not saved.")
        return 130
    except BootstrapError as error:
        hint = next((hint for code, hint in DOWNLOAD_FAILURES.values() if code == str(error)), None)
        if hint is not None:
            print(f"OpenRTL setup stopped: {error}. {hint}")
            return 2
        print("OpenRTL setup stopped: " + str(error) + ". Check consent, the selected wheel and private state; rerun ./openrtl doctor for readiness.")
        return 2
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired, zipfile.BadZipFile):
        print("OpenRTL setup stopped: dependency or state unavailable. Check connectivity or select an offline wheelhouse; no package hook or model call was run.")
        return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--fetch-wheel-with-consent"]:
        # Internal child protocol: an exact explicit download action, restricted
        # to the checked-in pin; no arbitrary URLs, credentials or input payloads.
        raise SystemExit(fetch_wheel_with_consent())
    raise SystemExit(main())
