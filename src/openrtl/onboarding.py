"""Private first-run preferences and local review, without provider authority.

This front door uses only the standard library until it dispatches an existing
design command. A saved model and credential *name* are preferences, never
permission to resolve a credential, contact a provider or start a simulator.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Sequence, cast
import uuid


SETUP_SCHEMA = "openrtl.setup.v1"
DEFAULT_MODEL = "gpt-5.4-nano-2026-03-17"
AGENTRIG_VERSION = "0.3.0"
OPENAI_SDK_VERSION = "2.47.0"
CONFIG_NAME = "setup.json"
MAX_CONFIG_BYTES = 16384


class SetupError(ValueError):
    """An intentionally bounded diagnostic; never carries user input."""


@dataclass(frozen=True)
class SetupConfig:
    model: str = DEFAULT_MODEL
    credential_env: str = "OPENAI_API_KEY"
    max_calls: int = 40
    max_repairs: int = 2
    max_output_tokens: int = 16000
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if (type(self.model) is not str or
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.model) is None or
                self.model.lower().startswith("sk-")):
            raise SetupError("model_identifier_invalid")
        if (type(self.credential_env) is not str or
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.credential_env) is None):
            raise SetupError("credential_environment_name_invalid")
        for selected, lower, upper in (
            (self.max_calls, 1, 200), (self.max_repairs, 0, 5),
            (self.max_output_tokens, 256, 32768), (self.timeout_seconds, 1, 300),
        ):
            if type(selected) is not int or not lower <= selected <= upper:
                raise SetupError("execution_bound_invalid")

    def document(self) -> dict[str, object]:
        return {"schema": SETUP_SCHEMA, **asdict(self)}


def validate_config(value: object) -> SetupConfig:
    fields = {"schema", "model", "credential_env", "max_calls", "max_repairs",
              "max_output_tokens", "timeout_seconds"}
    if type(value) is not dict:
        raise SetupError("setup_object_required")
    data = cast(dict[str, object], value)
    if set(data) != fields or data["schema"] != SETUP_SCHEMA:
        raise SetupError("setup_schema_or_fields_invalid")
    # Constructors validate types at runtime as well as the numerical bounds.
    return SetupConfig(model=cast(str, data["model"]),
                       credential_env=cast(str, data["credential_env"]),
                       max_calls=cast(int, data["max_calls"]),
                       max_repairs=cast(int, data["max_repairs"]),
                       max_output_tokens=cast(int, data["max_output_tokens"]),
                       timeout_seconds=cast(int, data["timeout_seconds"]))


def default_state_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenRTL"
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        selected = Path(base)
        if not selected.is_absolute():
            raise SetupError("xdg_state_directory_must_be_absolute")
        return selected / "openrtl"
    return Path.home() / ".local" / "state" / "openrtl"


def _private(info: os.stat_result, *, directory: bool) -> None:
    recognized = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not recognized or info.st_uid != os.getuid() or info.st_mode & 0o077 or
            (not directory and info.st_nlink != 1)):
        raise SetupError("setup_storage_must_be_private_owned_and_unlinked")


def _open_state(path: Path, *, create: bool) -> int:
    """Walk via directory descriptors so parent symlinks cannot redirect writes."""
    selected = path.absolute()
    if ".." in selected.parts or selected == Path(selected.anchor):
        raise SetupError("setup_directory_invalid")
    descriptor = os.open(selected.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in selected.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _private(os.fstat(descriptor), directory=True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def load_config(state_dir: Path) -> SetupConfig | None:
    try:
        directory = _open_state(state_dir, create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            descriptor = os.open(CONFIG_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            _private(info, directory=False)
            if info.st_size > MAX_CONFIG_BYTES:
                raise SetupError("setup_document_too_large")
            payload = stream.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise SetupError("setup_document_too_large")
        try:
            return validate_config(json.loads(payload))
        except (UnicodeError, json.JSONDecodeError):
            raise SetupError("setup_document_invalid") from None
    finally:
        os.close(directory)


def save_config(state_dir: Path, config: SetupConfig) -> None:
    """Atomic, private, non-link writes; interruption leaves prior setup usable."""
    payload = (json.dumps(config.document(), indent=2, sort_keys=True) + "\n").encode()
    directory = _open_state(state_dir, create=True)
    temporary = ".setup-" + uuid.uuid4().hex + ".tmp"
    created = False
    try:
        try:
            _private(os.stat(CONFIG_NAME, dir_fd=directory, follow_symlinks=False), directory=False)
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


def _package_version(package: str) -> str:
    try:
        selected = version(package)
    except PackageNotFoundError:
        return "not installed"
    return selected if re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", selected) else "unrecognized metadata"


def readiness(config: SetupConfig | None) -> dict[str, object]:
    packages = {name: _package_version(name) for name in ("openrtl", "agentrig", "openai")}
    local_ready = sys.version_info >= (3, 12) and packages["agentrig"] == AGENTRIG_VERSION
    sdk_ready = packages["openai"] == OPENAI_SDK_VERSION
    return {
        "schema": "openrtl.readiness.v1",
        "local_review_ready": local_ready,
        "provider_configuration_ready": config is not None and sdk_ready and local_ready,
        "provider_authorized": False,
        "simulation_ready": False,
        "live_qualified": False,
        "release_qualified": False,
        "local_specification_review": (
            "available; no provider or simulator required" if local_ready else
            "requires Python >=3.12 and exact AgentRig " + AGENTRIG_VERSION + "; use ./openrtl"),
        "setup": "configured" if config is not None else "not configured; run ./openrtl setup",
        "model": config.model if config is not None else "not selected",
        "model_qualification": "unverified; no automatic upgrade",
        "credential_source": "name configured; value never inspected" if config else "not configured",
        "packages": packages,
        "optional_provider_sdk": (
            "exact OpenAI SDK " + OPENAI_SDK_VERSION + " present; credential validity and access unverified" if sdk_ready else
            "requires optional OpenAI SDK " + OPENAI_SDK_VERSION + "; current: " + packages["openai"] +
            "; installation requires separate approval"),
        "provider": "not authorized; SDK version alone does not prove provider readiness",
        "simulation": "unconfigured; an explicitly approved isolated runtime is required",
        "live_qualification": "M46 pending; fixtures and the FIFO canary do not qualify new RTL design",
        "release": "M47 pending",
        "provider_calls": False,
        "credential_resolution": False,
        "simulation_performed": False,
    }


def show_readiness(config: SetupConfig | None, emit: Callable[[str], None] = print) -> None:
    report = readiness(config)
    emit("Local specification review: " + str(report["local_specification_review"]))
    emit("Setup: " + str(report["setup"]) + "; model: " + str(report["model"]))
    emit("Model qualification: " + str(report["model_qualification"]))
    packages = cast(dict[str, str], report["packages"])
    emit("Packages: " + "; ".join(name + " " + value for name, value in packages.items()))
    emit("Credential source: " + str(report["credential_source"]))
    emit("Optional provider SDK: " + str(report["optional_provider_sdk"]))
    emit("Provider: " + str(report["provider"]))
    emit("Simulation: " + str(report["simulation"]))
    emit("Live qualification: " + str(report["live_qualification"]))
    emit("Release: " + str(report["release"]))


def guided_setup(config: SetupConfig, *, read: Callable[[str], str] = input,
                 emit: Callable[[str], None] = print) -> SetupConfig:
    emit("Choose a model explicitly. The default is an unqualified small-model trial; OpenRTL never upgrades it automatically.")
    emit("Enter only the credential environment-variable NAME. Do not paste a key here.")
    model = read("Model identifier [" + config.model + "]: ").strip() or config.model
    credential = read("Credential variable name [" + config.credential_env + "]: ").strip() or config.credential_env
    # Reject invalid names before collecting further input; no raw values are logged.
    selected = replace(config, model=model, credential_env=credential)
    values: dict[str, int] = {}
    for field, label, bounds in (
        ("max_calls", "Maximum provider calls", "1–200"),
        ("max_repairs", "Maximum repairs", "0–5"),
        ("max_output_tokens", "Maximum output tokens per call", "256–32768"),
        ("timeout_seconds", "Timeout seconds per call", "1–300"),
    ):
        previous = cast(int, getattr(selected, field))
        answer = read(label + " (" + bounds + ") [" + str(previous) + "]: ").strip()
        try:
            values[field] = int(answer) if answer else previous
        except ValueError:
            raise SetupError("execution_bound_invalid") from None
    selected = replace(selected, max_calls=values["max_calls"], max_repairs=values["max_repairs"],
                       max_output_tokens=values["max_output_tokens"], timeout_seconds=values["timeout_seconds"])
    emit("Saving preferences does not authorize downloads, credential access, provider calls or simulation.")
    return selected


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="openrtl", description="Set up OpenRTL or open a local specification-review session.",
        epilog="Existing commands pass through unchanged, including chat, resume and batch. Use COMMAND --help for their options.")
    result.add_argument("--state-dir", type=Path, help="private OpenRTL state directory")
    result.add_argument("--project", type=Path, help="explicit new or existing local project for the interactive front door")
    commands = result.add_subparsers(dest="command")
    setup = commands.add_parser("setup", help="save bounded preferences, without granting execution permissions")
    setup.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
    setup.add_argument("--noninteractive", action="store_true", help="save explicit/default preferences without prompting")
    setup.add_argument("--model")
    setup.add_argument("--credential-env", help="environment-variable name only; never a key")
    setup.add_argument("--max-calls", type=int)
    setup.add_argument("--max-repairs", type=int)
    setup.add_argument("--max-output-tokens", type=int)
    setup.add_argument("--timeout-seconds", type=int)
    doctor = commands.add_parser("doctor", help="inspect local readiness without any runtime or provider contact")
    doctor.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--require-local", action="store_true", help="return status 2 unless exact local prerequisites are ready")
    return result


def _forward(argv: Sequence[str]) -> int:
    from openrtl.cli import main as run_command
    return run_command(argv)


def _forwarded_arguments(argv: Sequence[str]) -> list[str] | None:
    # A leading state-directory option belongs to the front door, and is not a
    # design-command argument. Do not read that directory for forwarded batch.
    offset = 0
    while offset < len(argv):
        if argv[offset] == "--state-dir" and offset + 1 < len(argv):
            offset += 2
        elif argv[offset].startswith("--state-dir="):
            offset += 1
        else:
            break
    rest = list(argv[offset:])
    if not rest or rest[0] in ("setup", "doctor", "--help", "-h", "--state-dir", "--project"):
        return None
    if rest[0].startswith("--project="):
        return None
    if rest[0] == "runtime" and offset:
        # Runtime preferences belong to the selected product state. Batch still
        # discards this front-door option and never reads onboarding state.
        return [rest[0], *argv[:offset], *rest[1:]]
    return rest


def _project(state_dir: Path, name: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) is None:
        raise SetupError("project_name_must_be_a_short_local_identifier")
    directory = _open_state(state_dir / "projects", create=True)
    try:
        try:
            _private(os.stat(name, dir_fd=directory, follow_symlinks=False), directory=True)
        except FileNotFoundError:
            pass
    finally:
        os.close(directory)
    return state_dir.absolute() / "projects" / name


def main(argv: Sequence[str] | None = None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    # In particular, batch does not inspect setup, resolve credentials or prompt.
    forwarded = _forwarded_arguments(selected)
    if forwarded is not None:
        return _forward(forwarded)
    arguments = parser().parse_args(selected)
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if arguments.command != "doctor" and not interactive and not getattr(arguments, "noninteractive", False):
            print("Interactive setup requires a terminal. Use ./openrtl setup --noninteractive for preferences, or an explicit chat/resume/batch command.")
            return 2
        state_dir = arguments.state_dir if arguments.state_dir is not None else default_state_dir()
        config = load_config(state_dir)
        if arguments.command == "doctor":
            report = readiness(config)
            if arguments.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                show_readiness(config)
            return 2 if arguments.require_local and not report["local_review_ready"] else 0
        if arguments.command == "setup":
            changes = {key: value for key, value in vars(arguments).items()
                       if key in SetupConfig.__dataclass_fields__ and value is not None}
            config = replace(config or SetupConfig(), **changes)
            if not arguments.noninteractive:
                config = guided_setup(config, read=input)
            save_config(state_dir, config)
            print("Preferences saved. Credential values were not read; no execution permission was saved.")
            show_readiness(config)
            return 0
        if config is None:
            config = guided_setup(SetupConfig(), read=input)
            save_config(state_dir, config)
        show_readiness(config)
        print("Local review can load /spec <JSON path> and review it with /show. Natural-language model assistance requires explicit --allow-provider on a chat/resume invocation.")
        project = arguments.project
        if project is None:
            name = input("Project name, reused to resume [first-design]: ").strip() or "first-design"
            project = _project(state_dir, name)
        command = "resume" if project.exists() else "chat"
        print("Opening " + command + " for the selected local project. Provider and simulation permissions are off.")
        return _forward([command, "--project", str(project), "--model", config.model,
                         "--credential-env", config.credential_env,
                         "--max-calls", str(config.max_calls), "--max-repairs", str(config.max_repairs),
                         "--max-output-tokens", str(config.max_output_tokens),
                         "--timeout-seconds", str(config.timeout_seconds)])
    except (EOFError, KeyboardInterrupt):
        print("Setup interrupted. Completed preferences remain resumable; no execution permission was granted.")
        return 130
    except SetupError as error:
        print("OpenRTL setup stopped: " + str(error) + ". Review the setup arguments and private state directory.")
        return 2
    except (OSError, ValueError, TypeError):
        print("OpenRTL setup stopped: local state or configuration unavailable. Check private directory ownership, permissions and setup options. No automatic installation was attempted.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
