"""Concrete Lima retirement ports preserve exact effects and recovery evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from agentrig.capabilities import BackendFailure
from agentrig.core import RunContext
from agentrig.integrations import GuestWorkspaceEndpoint
from agentrig.integrations.bounded_process import ProcessOutput, ProcessSpec
from agentrig.integrations.lima_configuration import LimaConfiguration
from openrtl.adapters.guest_retirement import (
    RetirementBackendDefinition,
    apply_guest_retirement,
    lima_retirement_registration,
    plan_guest_retirement,
)
from openrtl.adapters.lima_managed import (
    audit_lima_artifact_closure,
    lima_retirement_generation_digest,
)
from openrtl.adapters.lima_retirement_ports import (
    LimaCliRetirementPorts,
    lima_cli_retirement_registration,
)
from openrtl.adapters.managed_backend import _context, managed_backend_fence
from openrtl.domain.design_session import JsonObject


OPERATION = "e" * 32


class LimaRetirementPortsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
            prefix="olp-", dir="/tmp"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state_root = self.root
        (self.state_root / "transport").mkdir(mode=0o700)
        (self.state_root / "config").mkdir(mode=0o700)
        (self.state_root / "cache").mkdir(mode=0o700)
        (self.state_root / "lima").mkdir(mode=0o700)
        executable = self.state_root / "limactl"
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        image = b"reviewed fixture image; never executed"
        policy = LimaConfiguration(
            state_root=self.state_root,
            guest_image_sha256="sha256:" + hashlib.sha256(image).hexdigest(),
            guest_image_size=len(image),
        )
        (self.state_root / "configuration.json").write_bytes(policy.json_bytes())
        (self.state_root / "configuration.json").chmod(0o600)
        (self.state_root / "artifacts").mkdir(mode=0o700)
        (self.state_root / "artifacts/guest.raw").write_bytes(image)
        (self.state_root / "artifacts/guest.raw").chmod(0o600)
        self.instance_id = "c" * 32
        (self.state_root / "lima" / ("managed-" + self.instance_id)).mkdir(mode=0o700)
        closure = audit_lima_artifact_closure(executable, self.state_root)
        self.managed = closure.configuration(self.instance_id)
        service_lock = self.state_root / "transport/.service.lock"
        service_lock.write_bytes(b"")
        service_lock.chmod(0o600)
        lock_info = service_lock.lstat()
        self.service_lock: JsonObject = {
            "device": lock_info.st_dev,
            "inode": lock_info.st_ino,
            "uid": lock_info.st_uid,
        }
        self.endpoint = self.create_socket()
        self.control = self.root / "control"
        self.status = "Stopped"
        self.specifications: list[ProcessSpec] = []
        self.backend_configuration: JsonObject = {
            "managed_configuration": self.managed,
            "lifecycle_operation_id": "d" * 32,
            "service_lock": self.service_lock,
            "generation_digest": lima_retirement_generation_digest(
                self.managed, self.endpoint, self.service_lock
            ),
        }
        self.configuration = self.configuration_json()

    def create_socket(self) -> GuestWorkspaceEndpoint:
        path = self.state_root / "transport/workspace.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            info = path.lstat()
        finally:
            listener.close()
        return GuestWorkspaceEndpoint(
            root=path.parent,
            instance_id=self.instance_id,
            device=info.st_dev,
            inode=info.st_ino,
            uid=info.st_uid,
        )

    def configuration_json(self) -> str:
        return json.dumps(
            {
                "endpoint": {
                    "root": str(self.endpoint.root),
                    "instance_id": self.endpoint.instance_id,
                    "device": self.endpoint.device,
                    "inode": self.endpoint.inode,
                    "uid": self.endpoint.uid,
                },
                "backend_configuration": self.backend_configuration,
            }
        )

    async def execute(
        self, spec: ProcessSpec, context: RunContext
    ) -> ProcessOutput:
        self.specifications.append(spec)
        instance = "managed-" + self.instance_id
        values = [
            instance,
            str(self.state_root / "lima" / instance),
            self.status,
            "vz",
            "aarch64",
        ]
        output = "|".join(json.dumps(value) for value in values).encode()
        return ProcessOutput(exit_code=0, stdout=output, stderr=b"", truncated=False)

    def ports(self) -> LimaCliRetirementPorts:
        return LimaCliRetirementPorts(
            self.backend_configuration,
            self.endpoint,
            self.control,
            self.execute,
        )

    def registration(
        self, ports: LimaCliRetirementPorts
    ) -> dict[str, RetirementBackendDefinition]:
        return {
            "lima-vz-managed": lima_retirement_registration(
                fence_generation=ports.fence_generation,
                retire_socket=ports.retire_socket,
                observe_retired=ports.observe_retired,
            )
        }

    def test_exact_stale_forward_is_removed_and_lock_workspace_are_retained(self) -> None:
        ports = self.ports()
        registrations = self.registration(ports)
        plan = plan_guest_retirement(
            "lima-vz-managed",
            "retire",
            self.configuration,
            OPERATION,
            registrations,
        )
        result = apply_guest_retirement(
            "lima-vz-managed",
            "retire",
            self.configuration,
            OPERATION,
            str(plan["plan_digest"]),
            frozenset(plan["required_effects"]),
            self.control,
            registrations=registrations,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(ports.last_retirement_effect, "removed")
        self.assertFalse((self.endpoint.root / "workspace.sock").exists())
        self.assertTrue((self.endpoint.root / ".service.lock").is_file())
        self.assertTrue(self.endpoint.root.is_dir())
        self.assertGreaterEqual(len(self.specifications), 3)
        self.assertTrue(
            all(
                specification.argv[0] == self.managed["executable"]
                for specification in self.specifications
            )
        )

    def test_lima_removed_forward_requires_inspection_recovery_without_fake_unlink(self) -> None:
        os.unlink(self.endpoint.root / "workspace.sock")
        ports = self.ports()
        registrations = self.registration(ports)
        retire = plan_guest_retirement(
            "lima-vz-managed",
            "retire",
            self.configuration,
            OPERATION,
            registrations,
        )
        with self.assertRaisesRegex(ValueError, "runtime_retirement_execution_failed"):
            apply_guest_retirement(
                "lima-vz-managed",
                "retire",
                self.configuration,
                OPERATION,
                str(retire["plan_digest"]),
                frozenset(retire["required_effects"]),
                self.control,
                registrations=registrations,
            )
        inspect = plan_guest_retirement(
            "lima-vz-managed",
            "inspect",
            self.configuration,
            OPERATION,
            registrations,
        )
        result = apply_guest_retirement(
            "lima-vz-managed",
            "inspect",
            self.configuration,
            OPERATION,
            str(inspect["plan_digest"]),
            frozenset(inspect["required_effects"]),
            self.control,
            registrations=registrations,
        )
        self.assertEqual((result["status"], result["reconciled"]), ("reconciled", True))
        self.assertIsNone(ports.last_retirement_effect)
        self.assertTrue((self.endpoint.root / ".service.lock").is_file())

    def test_running_instance_fails_before_socket_removal(self) -> None:
        self.status = "Running"
        registration = {
            "lima-vz-managed": lima_cli_retirement_registration(self.execute)
        }
        plan = plan_guest_retirement(
            "lima-vz-managed",
            "retire",
            self.configuration,
            OPERATION,
            registration,
        )
        with self.assertRaisesRegex(ValueError, "runtime_retirement_execution_failed"):
            apply_guest_retirement(
                "lima-vz-managed",
                "retire",
                self.configuration,
                OPERATION,
                str(plan["plan_digest"]),
                frozenset(plan["required_effects"]),
                self.control,
                registrations=registration,
            )
        self.assertTrue((self.endpoint.root / "workspace.sock").exists())

    def test_replaced_service_lock_fails_before_socket_removal(self) -> None:
        ports = self.ports()
        registrations = self.registration(ports)
        plan = plan_guest_retirement(
            "lima-vz-managed",
            "retire",
            self.configuration,
            OPERATION,
            registrations,
        )
        lock = self.endpoint.root / ".service.lock"
        os.unlink(lock)
        lock.write_bytes(b"")
        lock.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "runtime_retirement_execution_failed"):
            apply_guest_retirement(
                "lima-vz-managed",
                "retire",
                self.configuration,
                OPERATION,
                str(plan["plan_digest"]),
                frozenset(plan["required_effects"]),
                self.control,
                registrations=registrations,
            )
        self.assertTrue((self.endpoint.root / "workspace.sock").exists())

    def test_generation_digest_changes_with_observed_forward_identity(self) -> None:
        original = lima_retirement_generation_digest(
            self.managed, self.endpoint, self.service_lock
        )
        changed = GuestWorkspaceEndpoint(
            root=self.endpoint.root,
            instance_id=self.endpoint.instance_id,
            device=self.endpoint.device,
            inode=self.endpoint.inode + 1,
            uid=self.endpoint.uid,
        )
        self.assertNotEqual(
            original,
            lima_retirement_generation_digest(
                self.managed, changed, self.service_lock
            ),
        )

    def test_shared_backend_fence_rejects_concurrent_lifecycle_control(self) -> None:
        context = _context(30)
        with managed_backend_fence(self.control, context):
            with self.assertRaisesRegex(BackendFailure, "backend_journal_busy"):
                with managed_backend_fence(self.control, context):
                    self.fail("nested fence must not be acquired")


if __name__ == "__main__":
    unittest.main()
