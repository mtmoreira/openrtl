"""OpenRTL retirement composition uses injected backends and durable recovery."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.capabilities import BackendFailure
from agentrig.core import RunContext
from agentrig.integrations import (
    GuestServiceRetirementAdapter,
    GuestWorkspaceEndpoint,
    LimaGuestServiceGeneration,
    LimaQuiescenceObservation,
    LimaRetirementObservation,
)
from openrtl.adapters.guest_retirement import (
    RetirementBackendDefinition,
    apply_guest_retirement,
    guest_retirement_status,
    lima_retirement_registration,
    plan_guest_retirement,
    retirement_registry,
)
from openrtl.adapters.lima_managed import (
    audit_lima_artifact_closure,
    lima_retirement_generation_digest,
)
from openrtl.domain.design_session import JsonObject
from openrtl.runtime_cli import add_runtime_command, run_runtime_command


OPERATION = "a" * 32


class ScriptedRetirementAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.retired = False
        self.fail_retire_once = False

    @contextmanager
    def quiescent(
        self, endpoint: GuestWorkspaceEndpoint, context: RunContext
    ) -> Iterator[None]:
        self.calls.append("fence-enter")
        try:
            yield None
        finally:
            self.calls.append("fence-exit")

    def retire(
        self, endpoint: GuestWorkspaceEndpoint, digest: str, context: RunContext
    ) -> None:
        self.calls.append("retire")
        self.retired = True
        if self.fail_retire_once:
            self.fail_retire_once = False
            raise OSError("private lost reply")

    def inspect_retired(
        self, endpoint: GuestWorkspaceEndpoint, context: RunContext
    ) -> None:
        self.calls.append("inspect")
        if not self.retired:
            raise BackendFailure("backend_staging_failed")


class GuestRetirementControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ScriptedRetirementAdapter()
        self.factories = 0

        def validate(value: JsonObject, endpoint: GuestWorkspaceEndpoint) -> JsonObject:
            if value != {"generation": "fixture"}:
                raise ValueError("fixture invalid")
            return dict(value)

        def create(
            configuration: JsonObject,
            endpoint: GuestWorkspaceEndpoint,
            state: Path,
        ) -> GuestServiceRetirementAdapter:
            self.factories += 1
            return self.adapter

        self.registrations = {
            "alternative": RetirementBackendDefinition(validate=validate, create=create)
        }
        self.configuration = json.dumps(
            {
                "endpoint": {
                    "root": "/private/fixture/transport",
                    "instance_id": "c" * 32,
                    "device": 4,
                    "inode": 5,
                    "uid": os.getuid(),
                },
                "backend_configuration": {"generation": "fixture"},
            }
        )

    def arguments(self, values: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest="command", required=True))
        return parser.parse_args(["runtime", *values])

    def test_plan_is_pure_backend_neutral_and_action_scoped(self) -> None:
        retire = plan_guest_retirement(
            "alternative", "retire", self.configuration, OPERATION, self.registrations
        )
        inspect = plan_guest_retirement(
            "alternative", "inspect", self.configuration, OPERATION, self.registrations
        )
        self.assertEqual(
            retire["required_effects"],
            ["guest_socket_remove", "local_write", "runtime_contact"],
        )
        self.assertEqual(inspect["required_effects"], ["local_write", "runtime_contact"])
        self.assertNotEqual(retire["plan_digest"], inspect["plan_digest"])
        self.assertFalse(retire["runtime_contact"])
        self.assertEqual(self.factories, 0)

    def test_changed_plan_or_effects_fail_before_state_and_adapter(self) -> None:
        plan = plan_guest_retirement(
            "alternative", "retire", self.configuration, OPERATION, self.registrations
        )
        for digest, effects, code in (
            (
                "sha256:" + "0" * 64,
                frozenset({"guest_socket_remove", "local_write", "runtime_contact"}),
                "plan_changed",
            ),
            (
                str(plan["plan_digest"]),
                frozenset({"local_write", "runtime_contact"}),
                "authority_required",
            ),
        ):
            with self.subTest(code=code), patch(
                "openrtl.adapters.guest_retirement._open_state",
                side_effect=AssertionError("must fail before state"),
            ):
                with self.assertRaisesRegex(ValueError, "runtime_retirement_" + code):
                    apply_guest_retirement(
                        "alternative",
                        "retire",
                        self.configuration,
                        OPERATION,
                        digest,
                        effects,
                        Path("/private/fixture"),
                        registrations=self.registrations,
                    )
        self.assertEqual(self.factories, 0)

    def test_durable_completion_status_and_operation_reuse(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ort-", dir="/tmp") as temporary:
            state = Path(temporary).resolve()
            plan = plan_guest_retirement(
                "alternative", "retire", self.configuration, OPERATION, self.registrations
            )
            effects = frozenset(plan["required_effects"])
            result = apply_guest_retirement(
                "alternative",
                "retire",
                self.configuration,
                OPERATION,
                str(plan["plan_digest"]),
                effects,
                state,
                registrations=self.registrations,
            )
            self.assertEqual((result["status"], result["observation"]), ("completed", "stopped"))
            self.assertEqual(
                self.adapter.calls, ["fence-enter", "retire", "inspect", "fence-exit"]
            )
            status = guest_retirement_status(state)
            self.assertEqual((status["status"], status["observation"]), ("completed", "stopped"))
            self.assertFalse(status["runtime_contact"])
            with self.assertRaisesRegex(ValueError, "runtime_retirement_operation_reused"):
                apply_guest_retirement(
                    "alternative",
                    "retire",
                    self.configuration,
                    OPERATION,
                    str(plan["plan_digest"]),
                    effects,
                    state,
                    registrations=self.registrations,
                )

    def test_lost_reply_reconciles_by_inspection_without_replaying_removal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orr-", dir="/tmp") as temporary:
            state = Path(temporary).resolve()
            self.adapter.fail_retire_once = True
            retire = plan_guest_retirement(
                "alternative", "retire", self.configuration, OPERATION, self.registrations
            )
            with self.assertRaisesRegex(ValueError, "runtime_retirement_execution_failed"):
                apply_guest_retirement(
                    "alternative",
                    "retire",
                    self.configuration,
                    OPERATION,
                    str(retire["plan_digest"]),
                    frozenset(retire["required_effects"]),
                    state,
                    registrations=self.registrations,
                )
            self.assertEqual(guest_retirement_status(state)["status"], "uncertain")
            inspect = plan_guest_retirement(
                "alternative", "inspect", self.configuration, OPERATION, self.registrations
            )
            result = apply_guest_retirement(
                "alternative",
                "inspect",
                self.configuration,
                OPERATION,
                str(inspect["plan_digest"]),
                frozenset(inspect["required_effects"]),
                state,
                registrations=self.registrations,
            )
            self.assertEqual((result["status"], result["reconciled"]), ("reconciled", True))
            self.assertEqual(self.adapter.calls.count("retire"), 1)

    def test_default_lima_plan_is_pure_and_concrete_registration_is_available(self) -> None:
        endpoint = GuestWorkspaceEndpoint(
            root=Path("/private/fixture/transport"),
            instance_id="c" * 32,
            device=4,
            inode=5,
            uid=os.getuid(),
        )
        managed: JsonObject = {
            "executable": "/private/bin/limactl",
            "executable_sha256": "sha256:" + "1" * 64,
            "state_root": "/private/fixture",
            "configuration_file": "/private/fixture/configuration.json",
            "configuration_sha256": "sha256:" + "2" * 64,
            "artifact_closure_sha256": "sha256:" + "3" * 64,
            "instance_id": "c" * 32,
        }
        service_lock: JsonObject = {"device": 6, "inode": 7, "uid": os.getuid()}
        configuration = json.dumps(
            {
                "endpoint": {
                    "root": str(endpoint.root),
                    "instance_id": endpoint.instance_id,
                    "device": endpoint.device,
                    "inode": endpoint.inode,
                    "uid": endpoint.uid,
                },
                "backend_configuration": {
                    "managed_configuration": managed,
                    "lifecycle_operation_id": "d" * 32,
                    "service_lock": service_lock,
                    "generation_digest": lima_retirement_generation_digest(
                        managed, endpoint, service_lock
                    ),
                },
            }
        )
        with patch(
            "agentrig.integrations.bounded_process.run_bounded_process",
            side_effect=AssertionError("pure planning must not contact Lima"),
        ), patch(
            "openrtl.adapters.guest_retirement._open_state",
            side_effect=AssertionError("pure planning must not create state"),
        ):
            plan = plan_guest_retirement(
                "lima-vz-managed", "retire", configuration, OPERATION
            )
            definition = retirement_registry()["lima-vz-managed"]
        self.assertEqual(
            plan["required_effects"],
            ["guest_socket_remove", "local_write", "runtime_contact"],
        )
        self.assertIsNotNone(definition.create)

    def test_missing_candidate_sdk_fails_before_state_and_preserves_injected_ports(self) -> None:
        with patch.dict("sys.modules", {"agentrig.capabilities.local_backend": None}), patch(
            "openrtl.adapters.guest_retirement._open_state",
            side_effect=AssertionError("missing SDK must not create state"),
        ):
            with self.assertRaisesRegex(
                ValueError, "runtime_retirement_sdk_candidate_required"
            ):
                plan_guest_retirement(
                    "lima-vz-managed", "retire", self.configuration, OPERATION
                )
            with self.assertRaisesRegex(
                ValueError, "runtime_retirement_sdk_candidate_required"
            ):
                apply_guest_retirement(
                    "lima-vz-managed",
                    "retire",
                    self.configuration,
                    OPERATION,
                    "sha256:" + "0" * 64,
                    frozenset(),
                    Path("/private/control"),
                )

        with patch.dict("sys.modules", {"agentrig.integrations.bounded_process": None}):
            with self.assertRaisesRegex(
                ValueError, "runtime_retirement_sdk_candidate_required"
            ):
                plan_guest_retirement(
                    "lima-vz-managed", "retire", self.configuration, OPERATION
                )
            injected = plan_guest_retirement(
                "alternative", "retire", self.configuration, OPERATION,
                self.registrations,
            )
        self.assertFalse(injected["runtime_contact"])
        self.assertEqual(self.factories, 0)

    def test_missing_candidate_sdk_has_bounded_cli_diagnostic(self) -> None:
        with patch.dict("sys.modules", {"agentrig.capabilities.local_backend": None}):
            output = io.StringIO()
            with redirect_stdout(output):
                code = run_runtime_command(
                    self.arguments(
                        [
                            "retirement-plan",
                            "--backend", "lima-vz-managed",
                            "--action", "retire",
                            "--config-json", self.configuration,
                            "--operation-id", OPERATION,
                            "--json",
                        ]
                    )
                )
        self.assertEqual(code, 2)
        self.assertIn("runtime_retirement_sdk_candidate_required", output.getvalue())

    def test_lima_registration_binds_audited_closure_and_injected_generation_ports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="olr-", dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            executable = root / "limactl"
            executable.write_bytes(b"#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            image = b"synthetic image, not bootable"
            image_sha256 = "sha256:" + hashlib.sha256(image).hexdigest()
            from agentrig.integrations.lima_configuration import LimaConfiguration

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
            (root / "transport").mkdir(mode=0o700)
            closure = audit_lima_artifact_closure(executable, root)
            endpoint: JsonObject = {
                "root": str(root / "transport"),
                "instance_id": "c" * 32,
                "device": (root / "transport").stat().st_dev,
                "inode": (root / "transport").stat().st_ino,
                "uid": os.getuid(),
            }
            endpoint_value = GuestWorkspaceEndpoint(
                root=Path(str(endpoint["root"])),
                instance_id=str(endpoint["instance_id"]),
                device=int(endpoint["device"]),
                inode=int(endpoint["inode"]),
                uid=int(endpoint["uid"]),
            )
            managed = closure.configuration("c" * 32)
            service_lock: JsonObject = {
                "device": endpoint_value.device,
                "inode": endpoint_value.inode,
                "uid": endpoint_value.uid,
            }
            configuration = json.dumps(
                {
                    "endpoint": endpoint,
                    "backend_configuration": {
                        "managed_configuration": managed,
                        "lifecycle_operation_id": "d" * 32,
                        "service_lock": service_lock,
                        "generation_digest": lima_retirement_generation_digest(
                            managed, endpoint_value, service_lock
                        ),
                    },
                }
            )
            calls: list[str] = []

            @contextmanager
            def fence(
                generation: LimaGuestServiceGeneration, context: RunContext
            ) -> Iterator[LimaQuiescenceObservation]:
                calls.append("fence-enter")
                try:
                    yield LimaQuiescenceObservation(binding_digest=generation.digest)
                finally:
                    calls.append("fence-exit")

            def retire(
                endpoint_value: GuestWorkspaceEndpoint, digest: str, context: RunContext
            ) -> None:
                calls.append("retire")

            def observe(
                generation: LimaGuestServiceGeneration, context: RunContext
            ) -> LimaRetirementObservation:
                calls.append("observe")
                return LimaRetirementObservation(binding_digest=generation.digest)

            registrations = {
                "lima-vz-managed": lima_retirement_registration(
                    fence_generation=fence,
                    retire_socket=retire,
                    observe_retired=observe,
                )
            }
            plan = plan_guest_retirement(
                "lima-vz-managed", "retire", configuration, OPERATION, registrations
            )
            result = apply_guest_retirement(
                "lima-vz-managed",
                "retire",
                configuration,
                OPERATION,
                str(plan["plan_digest"]),
                frozenset(plan["required_effects"]),
                root / "control",
                registrations=registrations,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(calls, ["fence-enter", "retire", "observe", "fence-exit"])

    def test_cli_routes_pure_plan_without_runtime_selection(self) -> None:
        planned = {"schema": "openrtl.guest-retirement-review.v1"}
        with patch(
            "openrtl.adapters.guest_retirement.plan_guest_retirement", return_value=planned
        ) as call, patch(
            "openrtl.runtime_cli.writer", side_effect=AssertionError("must not select")
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                code = run_runtime_command(
                    self.arguments(
                        [
                            "retirement-plan",
                            "--backend",
                            "alternative",
                            "--action",
                            "inspect",
                            "--config-json",
                            self.configuration,
                            "--operation-id",
                            OPERATION,
                            "--json",
                        ]
                    )
                )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), planned)
        call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
