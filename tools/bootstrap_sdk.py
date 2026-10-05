"""Optional, hash-locked SDK setup. Installation never grants model access."""

from __future__ import annotations

from email.parser import BytesParser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
from typing import cast
import uuid

from tools.bootstrap_openrtl import BootstrapError, read_regular, require


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "bootstrap/sdk-requirements.lock"
LOCK_SHA256 = "fa37eadceb2cb83402108b184adcfe2ee72743abcb66f1fe3ae674e17834c0b4"
MAX_FILE = 64 * 1024 * 1024
MAX_TREE = 256 * 1024 * 1024


def locked_packages() -> dict[str, str]:
    data = read_regular(LOCK, 256 * 1024)
    require(hashlib.sha256(data).hexdigest() == LOCK_SHA256, "sdk_lock_changed")
    rows = re.findall(r"^([a-z][a-z0-9-]*)==([0-9.]+) \\", data.decode(), re.MULTILINE)
    require(len(rows) == 17 and len(dict(rows)) == 17 and
            dict(rows).get("openai") == "2.47.0" and dict(rows).get("ollama") == "0.6.2",
            "sdk_lock_invalid")
    return dict(rows)


def cache_key() -> str:
    # Native wheels must never be reused by a different interpreter ABI/platform.
    identity = [sys.implementation.name, sys.implementation.cache_tag,
                sys.version, sys.platform, platform.machine(), LOCK_SHA256]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def private_directory(path: Path, *, create: bool) -> None:
    from openrtl.onboarding import _open_state
    descriptor = _open_state(path, create=create)
    os.close(descriptor)


def inventory(site: Path) -> dict[str, dict[str, str | int]]:
    private_directory(site, create=False)
    result: dict[str, dict[str, str | int]] = {}
    total = 0
    for directory, children, filenames in os.walk(site, followlinks=False):
        current = Path(directory)
        private_directory(current, create=False)
        for name in children:
            private_directory(current / name, create=False)
        for name in filenames:
            selected = current / name
            relative = selected.relative_to(site).as_posix()
            # certifi's public CA bundle is package data from its locked wheel,
            # not a user certificate or credential. No other PEM is accepted.
            require((not name.endswith((".pth", ".pyc", ".pyo", ".key", ".pem", ".p12", ".pfx"))
                     or relative == "certifi/cacert.pem")
                    and name not in (".env", ".netrc", ".git-credentials") and not name.startswith(".env."),
                    "sdk_member_rejected")
            data = read_regular(selected, MAX_FILE, private=True)
            total += len(data)
            require(total <= MAX_TREE and len(result) < 10000, "sdk_tree_limit")
            result[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    require(bool(result), "sdk_empty_installation")
    return result


def verify_metadata(site: Path) -> None:
    expected = locked_packages()
    observed: dict[str, str] = {}
    for directory in site.glob("*.dist-info"):
        private_directory(directory, create=False)
        metadata = BytesParser().parsebytes(read_regular(directory / "METADATA", 2 * 1024 * 1024, private=True))
        name = re.sub(r"[-_.]+", "-", str(metadata["Name"]).lower())
        require(name not in observed, "sdk_duplicate_distribution")
        observed[name] = str(metadata["Version"])
    require(observed == expected, "sdk_distribution_set_mismatch")


def cached_sdk(state: Path) -> Path | None:
    locked_packages()
    base = state / "sdk" / cache_key()
    for directory in (state, state / "sdk", base):
        try:
            private_directory(directory, create=False)
        except FileNotFoundError:
            return None
    receipt = base / "active.json"
    if not os.path.lexists(receipt):
        return None
    document = json.loads(read_regular(receipt, 2 * 1024 * 1024, private=True))
    require(type(document) is dict and set(document) == {"schema", "lock_sha256", "attempt", "files"}
            and document["schema"] == "openrtl.sdk-cache.v1" and document["lock_sha256"] == LOCK_SHA256
            and type(document["attempt"]) is str
            and re.fullmatch(r"attempt-[a-f0-9]{32}", document["attempt"]) is not None,
            "sdk_receipt_invalid")
    attempt = base / cast(str, document["attempt"])
    private_directory(attempt, create=False)
    site = attempt / "site-packages"
    require(inventory(site) == document["files"], "sdk_cache_changed")
    verify_metadata(site)
    return site


def run_bounded(argv: list[str], cwd: Path, environment: dict[str, str], *, timeout: int = 180,
                capture_path: bool = False) -> bytes:
    # Wheel-only uv installation spawns no builds. A dedicated process group
    # still ensures a timeout/interruption cannot leave our children running.
    import signal
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=output if capture_path else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True, umask=0o077)
        try:
            result = process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        require(result == 0, "sdk_setup_command_failed")
        output.seek(0)
        data = output.read(8193)
        require(len(data) <= 8192, "sdk_setup_output_limit")
        return data


def prepare_uv(state: Path, *, offline: bool, artifacts: Path | None) -> Path:
    command = ["/bin/sh", str(ROOT / "tools/bootstrap_runtime.sh"), "--prepare-uv",
               "--allow-runtime-install", "--state-dir", str(state)]
    if offline:
        command.append("--offline")
    if artifacts is not None:
        command.extend(["--runtime-artifacts", str(artifacts)])
    # Only a bounded path is returned. The helper has fixed download deadlines;
    # there are no credentials or existing uv configuration in its environment.
    data = run_bounded(command, ROOT, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                       timeout=150, capture_path=True)
    uv = Path(data.decode().strip())
    require(uv.is_absolute() and uv.is_relative_to(state / "runtime") and uv.name == "uv",
            "sdk_uv_path_rejected")
    read_regular(uv, MAX_FILE, private=True)
    return uv


def install_sdk(state: Path, *, authorized: bool, offline: bool,
                wheelhouse: Path | None = None, artifacts: Path | None = None) -> Path:
    require(authorized, "sdk_installation_consent_required")
    state = state.absolute()
    existing = cached_sdk(state)
    if existing is not None:
        return existing
    require(not offline or (wheelhouse is not None and artifacts is not None), "offline_sdk_artifacts_required")
    if wheelhouse is not None:
        selected = wheelhouse.absolute()
        require(selected.is_dir() and ".." not in selected.parts and
                not any(p.is_symlink() for p in (*selected.parents, selected)), "sdk_wheelhouse_rejected")
        wheelhouse = selected
    for cache_directory in (state, state / "sdk", state / "sdk" / cache_key()):
        private_directory(cache_directory, create=True)
    base = state / "sdk" / cache_key()
    descriptor = os.open(base / ".setup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and not info.st_mode & 0o077, "sdk_lock_rejected")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("sdk_setup_already_running") from None
        existing = cached_sdk(state)
        if existing is not None:
            return existing
        uv = prepare_uv(state, offline=offline, artifacts=artifacts)
        attempt = base / ("attempt-" + uuid.uuid4().hex)
        attempt.mkdir(mode=0o700)
        for name in ("home", "cache", "config", "data", "credentials", "site-packages"):
            (attempt / name).mkdir(mode=0o700)
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                       "HOME": str(attempt / "home"), "XDG_CONFIG_HOME": str(attempt / "config"),
                       "XDG_DATA_HOME": str(attempt / "data"), "UV_CREDENTIALS_DIR": str(attempt / "credentials"),
                       "UV_PYTHON_DOWNLOADS": "never", "UV_NO_PROGRESS": "1"}
        site = attempt / "site-packages"
        command = [str(uv), "--no-config", "--no-python-downloads", "--cache-dir", str(attempt / "cache"),
                   "pip", "install", "--python", sys.executable, "--target", str(site),
                   "--require-hashes", "--only-binary", ":all:", "--no-deps", "--link-mode", "copy",
                   "--keyring-provider", "disabled", "--requirements", str(LOCK)]
        if wheelhouse is not None:
            command.extend(["--no-index", "--find-links", str(wheelhouse)])
        else:
            command.extend(["--index-url", "https://pypi.org/simple"])
        if offline:
            command.append("--offline")
        run_bounded(command, attempt, environment)
        # uv copies wheel modes (usually 0644). Restrict only the fresh owned
        # attempt, rejecting links before chmod; never touch pre-existing files.
        for directory, children, filenames in os.walk(site, followlinks=False):
            for name in children + filenames:
                path = Path(directory) / name
                info = path.lstat()
                require(info.st_uid == os.getuid() and not stat.S_ISLNK(info.st_mode)
                        and (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)),
                        "sdk_install_member_rejected")
                path.chmod(0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
        verify_metadata(site)
        files = inventory(site)
        run_bounded([sys.executable, "-I", "-S", "-B", "-c",
                     "import sys; sys.path.insert(0, sys.argv[1]); import openai, ollama; "
                     "from importlib.metadata import version; "
                     "raise SystemExit(0 if openai.__version__ == '2.47.0' and "
                     "version('ollama') == '0.6.2' else 2)", str(site)],
                    attempt, environment, timeout=30)
        require(inventory(site) == files, "sdk_import_modified_cache")
        document = {"schema": "openrtl.sdk-cache.v1", "lock_sha256": LOCK_SHA256,
                    "attempt": attempt.name, "files": files}
        temporary = attempt / "receipt.json"
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(document, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        require(not os.path.lexists(base / "active.json"), "sdk_destination_changed")
        os.rename(temporary, base / "active.json")
        # Failed attempts remain inspectable and are never mistaken for active.
        return cast(Path, cached_sdk(state))
    finally:
        os.close(descriptor)
