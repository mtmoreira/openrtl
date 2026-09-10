"""Local runtime selection and self-test. Managed VM adoption remains explicit."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import uuid

from openrtl.adapters.design_simulation import IsolatedDesignSimulator
from openrtl.adapters.runtime_selection import local_identity, select_runtime, verify_local_identity
from openrtl.adapters.runtime_selftest import collateral, selftest_digest, verify_evidence
from openrtl.adapters.runtime_store import load, save, writer
from openrtl.domain.design_session import JsonObject, content_digest, require
from openrtl.domain.simulation_runtime import PROFILE_SCHEMA, RESOURCE_DEFAULTS, validate_profile, validate_receipt
from openrtl.onboarding import _open_state, default_state_dir


RECEIPT_SCHEMA = "openrtl.runtime-selftest.v1"


def add_runtime_command(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    runtime = subcommands.add_parser("runtime", help="plan, select or explicitly test one isolated runtime")
    runtime.add_argument("--state-dir", type=Path)
    commands = runtime.add_subparsers(dest="runtime_command", required=True)
    for name in ("plan", "status", "select", "self-test", "recover", "backends", "backend-plan", "backend-operation"):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
        command.add_argument("--json", action="store_true")
        if name == "backend-plan":
            command.add_argument("--backend", required=True)
            command.add_argument("--action", required=True, choices=("inspect", "prepare", "start", "stop"))
            command.add_argument("--config-json", required=True, help="bounded explicit backend configuration; review only")
            command.add_argument("--operation-id", help="optional 32 lowercase hex review identity")
        if name in ("select", "self-test", "recover"):
            command.add_argument("--allow-runtime-contact", action="store_true")
        if name == "select":
            command.add_argument("--docker", required=True)
            command.add_argument("--socket", required=True)
            command.add_argument("--image", required=True, help="exact existing local sha256 image ID; no pull")
            command.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
            command.add_argument("--python", default="/usr/local/bin/python3", help="absolute in-container interpreter")
            command.add_argument("--verilator", required=True, help="exact major.minor tool version")
            command.add_argument("--timeout-seconds", type=int, default=120)
            for key, value in RESOURCE_DEFAULTS.items():
                command.add_argument("--" + key.replace("_", "-"), type=int, default=value)


def plan() -> JsonObject:
    return {"schema": "openrtl.runtime-plan.v1", "host": sys.platform,
            "existing_runtime": "Explicit current-user rootless Docker only; no daemon discovery or contact by default",
            "managed_runtime": "Optional replaceable backend planning in SDK candidate; Lima VM and image qualification remain pending",
            "image": "Exact local image content ID and architecture; no automatic pulls or published image claim",
            "resources": dict(RESOURCE_DEFAULTS),
            "next_step": "Select a separately reviewed owned runtime and image, then authorize its fixed self-test",
            "effects": {"daemon_contact": False, "installation": False, "provider_calls": False},
            "m42_complete": False, "m46": "pending", "m47": "pending"}


def _profile(state: Path) -> JsonObject:
    selected = load(state, "runtime.json")
    require(selected is not None, "runtime_selection_missing")
    return validate_profile(selected)


def _attempt(state: Path, receipt: JsonObject) -> Path:
    validate_receipt(receipt)
    require(receipt.get("schema") == RECEIPT_SCHEMA and
            all(isinstance(receipt.get(key), str) and re.fullmatch(r"[a-f0-9]{32}", receipt[key]) is not None
                for key in ("attempt", "operation")), "runtime_receipt_invalid")
    attempt = receipt["attempt"]
    assert isinstance(attempt, str)
    return state.absolute() / "runtime-checks" / attempt


def ready_profile(state: Path) -> JsonObject:
    profile = _profile(state)
    receipt = load(state, "runtime-selftest.json")
    require(receipt is not None, "runtime_selftest_required")
    assert receipt is not None
    receipt = validate_receipt(receipt)
    require(receipt["status"] == "passed", "runtime_selftest_required")
    project = _attempt(state, receipt)
    require(receipt.get("profile_digest") == content_digest(profile) and
            receipt.get("selftest_digest") == selftest_digest(profile), "runtime_selftest_stale")
    from openrtl.adapters.runtime_selftest import _read
    report = json.loads(_read(project / "runs" / receipt["operation"] / "evidence/report.json", 64000))
    require(isinstance(report, dict), "runtime_selftest_report_invalid")
    require(receipt.get("report_digest") == content_digest(report), "runtime_selftest_report_changed")
    verify_evidence(project, profile, receipt["operation"], report)
    verify_local_identity(profile)
    require(load(state, "runtime.json") == profile and load(state, "runtime-selftest.json") == receipt,
            "runtime_selection_changed_during_review")
    return profile


def status(state: Path) -> JsonObject:
    selected = load(state, "runtime.json")
    if selected is None:
        return {**plan(), "status": "unconfigured", "local_selftest_verified": False}
    profile = validate_profile(selected)
    receipt = load(state, "runtime-selftest.json")
    if receipt is not None:
        receipt = validate_receipt(receipt)
    verified = False
    if receipt is not None and receipt.get("status") == "passed":
        ready_profile(state)
        verified = True
    return {"schema": "openrtl.runtime-status.v1", "status": receipt.get("status") if receipt else "self-test-required",
            "selection_digest": content_digest(profile), "architecture": profile["architecture"],
            "resources": profile["resources"], "local_selftest_verified": verified,
            "daemon_contact": False, "execution_authorized": False,
            "notice": "Local consistency only; daemon/image identity is checked again before execution",
            "m42_complete": False, "m46": "pending", "m47": "pending"}


def _new_attempt(state: Path) -> Path:
    attempt = state.absolute() / "runtime-checks" / uuid.uuid4().hex
    descriptor = _open_state(attempt, create=True)
    os.close(descriptor)
    return attempt


def _no_active_receipt(state: Path) -> None:
    prior = load(state, "runtime-selftest.json")
    if prior is not None:
        prior = validate_receipt(prior)
    require(prior is None or prior.get("status") in ("passed", "recovered", "not-started"),
            "runtime_recovery_required_before_retry_or_reselection")


async def _select(arguments: argparse.Namespace, state: Path) -> JsonObject:
    require(arguments.allow_runtime_contact is True, "runtime_contact_requires_explicit_consent")
    # Validate syntax before inspecting even the selected local paths.
    candidate = validate_profile({"schema": PROFILE_SCHEMA, "docker_executable": arguments.docker,
        "socket": arguments.socket, "image_id": arguments.image, "python_executable": arguments.python,
        "verilator_version": arguments.verilator, "timeout_seconds": arguments.timeout_seconds,
        "architecture": arguments.architecture, "resources": {key: getattr(arguments, key) for key in RESOURCE_DEFAULTS},
        "docker_sha256": "sha256:" + "0" * 64, "socket_identity": {"device": 0, "inode": 0, "uid": 0},
        "daemon_id": "unobserved", "ownership": "current-user-rootless"})
    _no_active_receipt(state)
    candidate.update(local_identity(arguments.docker, arguments.socket))
    attempt = _new_attempt(state)
    config = attempt / "docker-config"
    config.mkdir(mode=0o700)
    runtime = IsolatedDesignSimulator(attempt, candidate)
    selected = await select_runtime(candidate, config, runtime._process, authorized=True)
    save(state, "runtime.json", selected)
    return {"status": "selected-self-test-required", "selection_digest": content_digest(selected),
            "execution_authorized": False, "installation": False, "image_pull": False}


async def _selftest(arguments: argparse.Namespace, state: Path) -> JsonObject:
    require(arguments.allow_runtime_contact is True, "runtime_contact_requires_explicit_consent")
    _no_active_receipt(state)
    profile = _profile(state)
    project = _new_attempt(state)
    operation = uuid.uuid4().hex
    receipt: JsonObject = {"schema": RECEIPT_SCHEMA, "attempt": project.name, "operation": operation,
        "profile_digest": content_digest(profile), "selftest_digest": selftest_digest(profile), "status": "not-started"}
    # Persist uncertainty before creating a simulator or making any daemon call.
    save(state, "runtime-selftest.json", receipt)
    try:
        runtime = IsolatedDesignSimulator(project, profile)
        receipt["status"] = "running"
        save(state, "runtime-selftest.json", receipt)
        files, manifest = collateral(profile)
        report = await runtime.simulate(files, manifest, selftest_digest(profile), operation)
        verify_evidence(project, profile, operation, report)
        receipt.update(status="passed", report_digest=content_digest(report))
        save(state, "runtime-selftest.json", receipt)
    except BaseException:
        if receipt["status"] != "not-started":
            receipt["status"] = "needs-recovery"
            receipt.pop("report_digest", None)
        save(state, "runtime-selftest.json", receipt)
        raise
    return {"status": "self-test-passed", "selection_digest": content_digest(profile),
            "evidence": str(project), "evidence_kind": "fixed-infrastructure-selftest-not-agent-designed-rtl",
            "execution_authorized": False, "m46": "pending", "m47": "pending"}


async def _recover(arguments: argparse.Namespace, state: Path) -> JsonObject:
    require(arguments.allow_runtime_contact is True, "runtime_contact_requires_explicit_consent")
    receipt = load(state, "runtime-selftest.json")
    require(receipt is not None, "runtime_recovery_not_pending")
    assert receipt is not None
    receipt = validate_receipt(receipt)
    require(receipt["status"] in ("running", "needs-recovery"), "runtime_recovery_not_pending")
    profile = _profile(state)
    require(receipt.get("profile_digest") == content_digest(profile), "runtime_recovery_selection_changed")
    project = _attempt(state, receipt)
    from openrtl.adapters.design_session_store import safe_root
    intent = safe_root(project / "runs" / receipt["operation"] / "intent.json")
    require(intent.is_file(), "runtime_intent_missing_manual_reconciliation_required")
    runtime = IsolatedDesignSimulator(project, profile)
    await runtime.abandon(receipt["operation"], receipt["selftest_digest"])
    receipt["status"] = "recovered"
    save(state, "runtime-selftest.json", receipt)
    return {"status": "recovered-without-replaying-self-test", "evidence_retained": True, "execution_authorized": False}


def run_runtime_command(arguments: argparse.Namespace) -> int:
    try:
        if arguments.runtime_command == "plan":
            result = plan()
        elif arguments.runtime_command in ("backends", "backend-plan"):
            from openrtl.adapters.backend_setup import backends, review_backend
            result = (backends() if arguments.runtime_command == "backends" else
                      review_backend(arguments.backend, arguments.action, arguments.config_json,
                                     arguments.operation_id or uuid.uuid4().hex))
        elif arguments.runtime_command == 'backend-operation':
            from openrtl.adapters.backend_setup import backend_operation_status
            result = backend_operation_status(arguments.state_dir or default_state_dir())
        else:
            if arguments.runtime_command in ("select", "self-test", "recover"):
                require(arguments.allow_runtime_contact is True, "runtime_contact_requires_explicit_consent")
            state = arguments.state_dir if arguments.state_dir is not None else default_state_dir()
            if arguments.runtime_command == "status":
                result = status(state)
            else:
                action = {"select": _select, "self-test": _selftest, "recover": _recover}[arguments.runtime_command]
                with writer(state):
                    result = asyncio.run(action(arguments, state))
        if arguments.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif arguments.runtime_command in ("backends", "backend-plan", "backend-operation"):
            print(json.dumps(result, indent=2, sort_keys=True))
        elif arguments.runtime_command == "plan":
            print("Simulation setup: select a reviewed runtime owned by your account and an exact existing image.")
            print("This candidate can inspect current-user rootless Docker. Managed macOS VM setup is still pending.")
            print("Default limits: 2 CPUs, 2048 MiB memory, 128 processes and 256 MiB output.")
            print("No daemon was contacted. Downloads, installation and the fixed self-test need separate consent.")
        elif arguments.runtime_command == "status":
            print("Runtime: " + str(result["status"]))
            print("Fixed self-test evidence: " + ("verified locally" if result["local_selftest_verified"] else "not verified"))
            print("No daemon was contacted and no execution permission was restored.")
        elif arguments.runtime_command == "select":
            print("Runtime selected. Review and explicitly run runtime self-test before using it for designs.")
            print("No image was pulled and no container was started.")
        elif arguments.runtime_command == "self-test":
            print("Fixed runtime self-test passed. Evidence: " + str(result["evidence"]))
            print("This checks infrastructure; it does not establish agent-generated RTL correctness.")
        else:
            print("Recorded runtime operation reconciled. Evidence retained; the self-test was not replayed.")
        return 0
    except KeyboardInterrupt:
        print("Runtime operation interrupted. Evidence is retained; run runtime status before an explicit recovery.")
        return 130
    except (OSError, ValueError, TypeError, KeyError) as error:
        hints = {
            "runtime_backend_journal_invalid": "The local backend operation record cannot be verified. Preserve it for review; no runtime was contacted.",
            "runtime_backend_sdk_candidate_required": "This optional command needs the reviewed local AgentRig SDK candidate. The published bootstrap and existing runtime commands remain available.",
            "runtime_backend_configuration_invalid": "Review the backend's exact configuration, action, pins and resource bounds. No state or runtime was changed.",
            "runtime_backend_unavailable": "Select an explicitly registered backend; no fallback or discovery is performed.",
            "runtime_contact_requires_explicit_consent": "Review the selected endpoint and effects, then explicitly allow runtime contact.",
            "runtime_ownership_unqualified": "Use a reviewed current-user rootless runtime. Shared Docker Desktop or an unqualified VM is not selected automatically.",
            "runtime_socket_not_current_user": "Select your own private socket; do not change another account's runner or global Docker context.",
            "runtime_local_identity_changed": "The executable or endpoint changed. Preserve pending operations and review the runtime before reselection.",
            "runtime_image_or_architecture_changed": "The selected image or architecture differs from the reviewed pin. No image was pulled.",
            "runtime_pinned_image_unavailable": "The exact local image is unavailable. Image acquisition requires a separate reviewed action.",
            "runtime_writer_active": "Another runtime operation owns this state. Wait for it to finish; do not remove its lock.",
            "runtime_recovery_required_before_retry_or_reselection": "Inspect retained state and use explicit runtime recover before retrying or changing selection.",
            "runtime_intent_missing_manual_reconciliation_required": "Ownership evidence is missing. Manual reconciliation is required; no container was guessed or removed.",
            "runtime_selection_missing": "Use runtime plan, then select an approved owned endpoint and image.",
            "runtime_selftest_required": "Explicitly run the fixed runtime self-test before using this selection for designs.",
        }
        code = str(error) if type(error) is ValueError and str(error) in hints else "runtime_local_validation_failed"
        print("Runtime operation stopped: " + code + ". " + hints.get(code,
              "Check the reviewed pins, bounded resources, private state and retained evidence. No fallback, download or automatic retry occurred."))
        return 2
