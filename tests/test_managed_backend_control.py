"""Managed lifecycle composition remains replaceable and fail closed."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.capabilities.local_backend import (
    BackendAction,
    BackendAuthority,
    BackendDescriptor,
    BackendEffect,
    BackendFailure,
    BackendPlan,
    BackendRequest,
    BackendResult,
    check_backend_call,
)
from openrtl.adapters.lima_managed import LimaArtifactVerifier, audit_lima_artifact_closure
from openrtl.adapters.managed_backend import (
    ManagedBackendFactory,
    apply_managed_backend,
    plan_managed_backend,
)
from openrtl.runtime_cli import add_runtime_command, run_runtime_command


OPERATION = "a" * 32
CONFIGURATION = json.dumps({"fixture": True})


@dataclass(slots=True)
class AlternativeManagedBackend:
    calls: list[tuple[str, BackendAuthority]] = field(default_factory=list)
    descriptor: BackendDescriptor = field(
        default=BackendDescriptor(
            backend_id="alternative-managed",
            version="1",
            capabilities=frozenset({"managed-lifecycle.v1"}),
            actions=frozenset(BackendAction),
        ),
        init=False,
    )

    def plan(self, request: BackendRequest) -> BackendPlan:
        if dict(request.configuration) != {"fixture": True}:
            raise ValueError("fixture configuration invalid")
        return BackendPlan(
            descriptor=self.descriptor,
            request=request,
            effects=frozenset({BackendEffect.LOCAL_WRITE, BackendEffect.RUNTIME_CONTACT}),
            material={"fixture": "bounded"},
        )

    async def apply(
        self, plan: BackendPlan, authority: BackendAuthority, context: object
    ) -> BackendResult:
        check_backend_call(self, plan, authority, context)  # type: ignore[arg-type]
        self.calls.append(("apply", authority))
        return BackendResult(
            plan_digest=plan.digest,
            operation_id=plan.request.operation_id,
            status="running",
            binding={"transport": "alternative"},
        )

    async def reconcile(
        self, plan: BackendPlan, authority: BackendAuthority, context: object
    ) -> BackendResult:
        check_backend_call(self, plan, authority, context)  # type: ignore[arg-type]
        self.calls.append(("reconcile", authority))
        return BackendResult(
            plan_digest=plan.digest,
            operation_id=plan.request.operation_id,
            status="stopped",
            binding={"transport": "alternative"},
        )


class ManagedBackendControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = AlternativeManagedBackend()
        self.factory: ManagedBackendFactory = lambda state: self.backend
        self.registrations = {"alternative": self.factory}

    def arguments(self, values: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest="command", required=True))
        return parser.parse_args(["runtime", *values])

    def test_plan_is_pure_and_alternative_backend_uses_same_consumer(self) -> None:
        state = Path("/private/does-not-exist")
        result = plan_managed_backend(
            "alternative", "start", CONFIGURATION, OPERATION, state, self.registrations
        )
        self.assertEqual(result["backend"], "alternative")
        self.assertEqual(result["required_effects"], ["local_write", "runtime_contact"])
        self.assertFalse(result["runtime_contact"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(self.backend.calls, [])

    def test_apply_requires_exact_plan_and_effects_before_state_write(self) -> None:
        state = Path("/private/fixture")
        plan = plan_managed_backend(
            "alternative", "start", CONFIGURATION, OPERATION, state, self.registrations
        )
        for digest, effects, code in (
            ("sha256:" + "0" * 64, frozenset({"local_write", "runtime_contact"}), "plan_changed"),
            (plan["plan_digest"], frozenset({"runtime_contact"}), "authority_required"),
        ):
            with self.subTest(code=code), patch(
                "openrtl.adapters.managed_backend._open_state",
                side_effect=AssertionError("must fail before state write"),
            ):
                with self.assertRaisesRegex(ValueError, "runtime_backend_" + code):
                    asyncio.run(
                        apply_managed_backend(
                            "alternative",
                            "start",
                            CONFIGURATION,
                            OPERATION,
                            str(digest),
                            effects,
                            state,
                            registrations=self.registrations,
                        )
                    )
        self.assertEqual(self.backend.calls, [])

    def test_apply_and_reconcile_pass_invocation_local_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omc-", dir="/tmp") as temporary:
            state = Path(temporary).resolve()
            operations = state / "backend-operations"
            operations.mkdir(mode=0o700)
            open_operations = lambda path, create: os.open(  # noqa: E731
                operations, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            plan = plan_managed_backend(
                "alternative", "start", CONFIGURATION, OPERATION, state, self.registrations
            )
            with patch("openrtl.adapters.managed_backend._open_state", side_effect=open_operations):
                result = asyncio.run(
                    apply_managed_backend(
                        "alternative",
                        "start",
                        CONFIGURATION,
                        OPERATION,
                        str(plan["plan_digest"]),
                        frozenset({"local_write", "runtime_contact"}),
                        state,
                        registrations=self.registrations,
                    )
                )
            self.assertEqual(result["status"], "running")
            self.assertFalse(result["ready_for_simulation"])
            self.assertFalse(result["execution_authorized"])
            self.assertEqual(self.backend.calls[0][0], "apply")
            self.assertEqual(
                {effect.value for effect in self.backend.calls[0][1].effects},
                {"local_write", "runtime_contact"},
            )

            inspection = plan_managed_backend(
                "alternative", "inspect", CONFIGURATION, OPERATION, state, self.registrations
            )
            with patch("openrtl.adapters.managed_backend._open_state", side_effect=open_operations):
                reconciled = asyncio.run(
                    apply_managed_backend(
                        "alternative",
                        "inspect",
                        CONFIGURATION,
                        OPERATION,
                        str(inspection["plan_digest"]),
                        frozenset({"local_write", "runtime_contact"}),
                        state,
                        reconcile=True,
                        registrations=self.registrations,
                    )
                )
            self.assertTrue(reconciled["reconciled"])
            self.assertEqual(self.backend.calls[-1][0], "reconcile")

    def test_cli_routes_managed_operations_without_generic_runtime_selection(self) -> None:
        planned = {"schema": "openrtl.managed-backend-plan.v1"}
        with patch(
            "openrtl.adapters.managed_backend.plan_managed_backend", return_value=planned
        ) as call, patch("openrtl.runtime_cli.writer", side_effect=AssertionError("must not select")):
            output = io.StringIO()
            with redirect_stdout(output):
                code = run_runtime_command(
                    self.arguments(
                        [
                            "managed-plan",
                            "--backend",
                            "alternative",
                            "--action",
                            "inspect",
                            "--config-json",
                            CONFIGURATION,
                            "--operation-id",
                            OPERATION,
                            "--state-dir",
                            "/private/fixture",
                            "--json",
                        ]
                    )
                )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), planned)
        call.assert_called_once()

    def test_lima_closure_is_content_bound_and_tamper_is_rejected(self) -> None:
        from agentrig.integrations.lima_configuration import LimaConfiguration
        from agentrig.integrations.lima_lifecycle import LimaLifecycleBackend
        from agentrig.testing import MemoryBackendJournal

        with tempfile.TemporaryDirectory(prefix="olc-", dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            executable = root / "limactl"
            executable.write_bytes(b"#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            image = b"synthetic image, not bootable"
            image_sha256 = "sha256:" + hashlib.sha256(image).hexdigest()
            policy = LimaConfiguration(
                state_root=root,
                guest_image_sha256=image_sha256,
                guest_image_size=len(image),
            )
            (root / "configuration.json").write_bytes(policy.json_bytes())
            (root / "configuration.json").chmod(0o600)
            (root / "artifacts").mkdir(mode=0o700)
            (root / "artifacts/guest.raw").write_bytes(image)
            (root / "artifacts/guest.raw").chmod(0o600)

            closure = audit_lima_artifact_closure(executable, root)
            config = closure.configuration("c" * 32)
            backend = LimaLifecycleBackend(
                journal=MemoryBackendJournal(), verify_artifacts=LimaArtifactVerifier()
            )
            plan = backend.plan(
                BackendRequest(
                    action=BackendAction.INSPECT,
                    operation_id=OPERATION,
                    configuration=config,
                )
            )
            self.assertFalse(LimaArtifactVerifier()(plan))
            executable.write_bytes(b"#!/bin/sh\nexit 1\n")
            with self.assertRaisesRegex(BackendFailure, "backend_artifacts_invalid"):
                LimaArtifactVerifier()(plan)


if __name__ == "__main__":
    unittest.main()
