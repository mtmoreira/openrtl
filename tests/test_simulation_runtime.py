"""Synthetic runtime contracts and process doubles; no Docker or qualification."""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from openrtl.adapters.design_simulation import IsolatedDesignSimulator
from openrtl.adapters.workload_transport import LimaWorkloadTransport
from openrtl.adapters.runtime_selection import inspect_selection, local_identity, select_runtime, verify_local_identity
from openrtl.adapters.runtime_selftest import collateral, selftest_digest, verify_evidence
from openrtl.adapters import runtime_store
from openrtl.domain.design_session import JsonObject, content_digest
from openrtl.domain.simulation_runtime import PROFILE_SCHEMA, RESOURCE_DEFAULTS, resource_arguments, validate_profile
from openrtl.runtime_cli import add_runtime_command, ready_profile, run_runtime_command


def profile() -> JsonObject:
    return {"schema": PROFILE_SCHEMA, "docker_executable": "/unit-only/docker", "socket": "/unit-only/socket",
            "image_id": "sha256:" + "a" * 64, "python_executable": "/usr/local/bin/python3",
            "verilator_version": "5.046", "timeout_seconds": 120, "architecture": "arm64",
            "resources": dict(RESOURCE_DEFAULTS), "docker_sha256": "sha256:" + "b" * 64,
            "socket_identity": {"device": 1, "inode": 2, "uid": 501},
            "daemon_id": "synthetic-daemon", "ownership": "current-user-rootless"}


def info(*, rootless: bool = True, architecture: str = "aarch64", daemon: str = "synthetic-daemon") -> bytes:
    return "|".join(json.dumps(value) for value in
                    (daemon, "linux", architecture, ["name=rootless"] if rootless else [], 4, 4*1024**3)).encode()


class RuntimeContractTest(unittest.TestCase):
    def test_profile_copies_nested_limits_and_never_contains_authority(self) -> None:
        candidate = profile()
        selected = validate_profile(candidate)
        candidate["resources"]["cpus"] = 8
        self.assertEqual(selected["resources"]["cpus"], 2)
        selected["authorized"] = True
        with self.assertRaises(ValueError):
            validate_profile(selected)

    def test_unknown_fields_bad_paths_hashes_ownership_and_boolean_bounds_fail_closed(self) -> None:
        mutations = [("schema", "unknown"), ("docker_executable", "/x/../docker"), ("socket", "/tmp/a\nsecret"),
                     ("python_executable", "python"), ("image_id", "image:latest"), ("timeout_seconds", True),
                     ("docker_sha256", "a"*64), ("architecture", "riscv64"), ("ownership", "shared-desktop"),
                     ("socket_identity", {"device": 1, "inode": 2, "uid": True}), ("daemon_id", "x\nsecret")]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_profile({**profile(), key: value})
        for key in RESOURCE_DEFAULTS:
            with self.subTest(resource=key), self.assertRaises(ValueError):
                validate_profile({**profile(), "resources": {**RESOURCE_DEFAULTS, key: True}})

    def test_resources_are_bounded_and_legacy_limits_remain_equivalent(self) -> None:
        self.assertEqual(resource_arguments(profile()), resource_arguments({"schema": "openrtl.design-container.v1"}))
        candidate = profile()
        candidate["resources"].update(cpus=1, memory_mib=1024, output_mib=128, pids=32)
        self.assertIn("--cpus=1", resource_arguments(validate_profile(candidate)))
        candidate["resources"]["output_mib"] = 1024
        with self.assertRaisesRegex(ValueError, "output_memory"):
            validate_profile(candidate)


class RuntimeTemporaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "empty-config"
        self.config.mkdir(mode=0o700)


class RuntimeInspectionTest(RuntimeTemporaryTest):
    def responses(self, daemon: bytes | None = None, image: bytes | None = None) -> AsyncMock:
        return AsyncMock(side_effect=[(0, daemon or info()),
            (0, image or ('"sha256:' + 'a'*64 + '"|"linux"|"arm64"').encode())])

    def test_denial_precedes_local_checks_and_processes(self) -> None:
        process = self.responses()
        with patch("openrtl.adapters.runtime_selection.verify_local_identity") as check:
            with self.assertRaisesRegex(ValueError, "consent"):
                asyncio.run(inspect_selection(profile(), self.config, process, authorized=False))
            check.assert_not_called()
            process.assert_not_called()

    def test_executable_bytes_and_socket_ownership_are_bound_without_connecting(self) -> None:
        binary, endpoint = self.root / "docker", self.root / "socket"
        binary.write_bytes(b"synthetic non-executed binary bytes")
        binary.chmod(0o700)
        endpoint.write_bytes(b"")
        endpoint.chmod(0o600)
        original = Path.lstat
        def lstat(path: Path) -> os.stat_result:
            info = original(path)
            if path == endpoint:
                values = list(info)
                values[0] = stat.S_IFSOCK | 0o600
                return os.stat_result(values)
            return info
        with patch.object(Path, "lstat", lstat):
            identity = local_identity(str(binary), str(endpoint))
            self.assertEqual(identity["docker_sha256"], "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest())
            selected = {**profile(), "docker_executable": str(binary), "socket": str(endpoint), **identity}
            verify_local_identity(selected)
            binary.write_bytes(b"changed synthetic bytes")
            with self.assertRaisesRegex(ValueError, "identity_changed"):
                verify_local_identity(selected)
        binary_link = self.root / "linked-docker"
        binary_link.symlink_to(binary)
        with self.assertRaisesRegex(ValueError, "symlink"):
            local_identity(str(binary_link), str(endpoint))

    def test_other_owner_or_shared_socket_rejected_before_binary_read(self) -> None:
        binary, endpoint = self.root / "docker", self.root / "socket"
        binary.write_bytes(b"synthetic")
        binary.chmod(0o700)
        endpoint.write_bytes(b"")
        original = Path.lstat
        for mode, uid in ((stat.S_IFSOCK | 0o600, os.getuid()+1), (stat.S_IFSOCK | 0o666, os.getuid())):
            def lstat(path: Path) -> os.stat_result:
                info = original(path)
                if path == endpoint:
                    values = list(info); values[0] = mode; values[4] = uid
                    return os.stat_result(values)
                return info
            with patch.object(Path, "lstat", lstat), patch("openrtl.adapters.runtime_selection.hashlib.file_digest") as digest:
                with self.assertRaises(ValueError):
                    local_identity(str(binary), str(endpoint))
                digest.assert_not_called()

    def test_exact_endpoint_config_image_and_bounded_read_only_queries(self) -> None:
        process = self.responses()
        with patch("openrtl.adapters.runtime_selection.verify_local_identity") as check:
            selected = asyncio.run(inspect_selection(profile(), self.config, process, authorized=True))
        self.assertEqual(selected, profile())
        self.assertEqual(check.call_count, 2)
        for call in process.call_args_list:
            argv, config, timeout, bound = call.args
            self.assertEqual(argv[:5], ["/unit-only/docker", "--host", "unix:///unit-only/socket", "--config", str(self.config)])
            self.assertEqual((config, timeout, bound), (self.config, 30, 8192))
            self.assertFalse(any(value in argv for value in ("pull", "start", "create", "rm", "context", "login")))
        self.assertEqual(process.call_args_list[1].args[0][-1], profile()["image_id"])

    def test_rootful_daemon_rejected_before_image_inspection(self) -> None:
        process = self.responses(info(rootless=False))
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            with self.assertRaisesRegex(ValueError, "ownership"):
                asyncio.run(inspect_selection(profile(), self.config, process, authorized=True))
        self.assertEqual(process.call_count, 1)

    def test_changed_daemon_architecture_or_image_refused_and_new_selection_binds_identity(self) -> None:
        for daemon, image in ((info(daemon="changed"), None), (info(architecture="x86_64"), None),
                              (info(), b'"sha256:other"|"linux"|"arm64"')):
            with self.subTest(daemon=daemon), patch("openrtl.adapters.runtime_selection.verify_local_identity"):
                with self.assertRaises(ValueError):
                    asyncio.run(inspect_selection(profile(), self.config, self.responses(daemon, image), authorized=True))
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            selected = asyncio.run(select_runtime(profile(), self.config, self.responses(info(daemon="new")), authorized=True))
        self.assertEqual(selected["daemon_id"], "new")

    def test_nonempty_config_bad_output_and_local_identity_changes_stop(self) -> None:
        (self.config / "config.json").write_text("{}")
        process = self.responses()
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"), self.assertRaisesRegex(ValueError, "empty"):
            asyncio.run(inspect_selection(profile(), self.config, process, authorized=True))
        process.assert_not_called()
        (self.config / "config.json").unlink()
        for raw in (b"not json", b"x"*8193, b'"name"|null|null|null|null|null'):
            with patch("openrtl.adapters.runtime_selection.verify_local_identity"), self.assertRaises(ValueError):
                asyncio.run(inspect_selection(profile(), self.config, self.responses(raw), authorized=True))
        with patch("openrtl.adapters.runtime_selection.verify_local_identity", side_effect=ValueError("changed")):
            process = self.responses()
            with self.assertRaises(ValueError):
                asyncio.run(inspect_selection(profile(), self.config, process, authorized=True))
            process.assert_not_called()


WAVES = b'''$timescale 1 ns $end
$scope module TOP $end
$var wire 1 ! y $end
$upscope $end
$enddefinitions $end
#0
0!
#2
1!
#6
0!
#8
1!
#12
'''


class RuntimeSimulationTest(RuntimeTemporaryTest):
    def simulation_response(self) -> bytes:
        artifacts = {"results.xml": b'<testsuite><testcase name="runtime_truth_table"/></testsuite>',
                     "model-results.json": b'{"tests":2,"passed":true}', "waves.vcd": WAVES,
                     "runner.log": b"synthetic process fixture, not real runtime qualification\n",
                     "toolchain.json": b'{"cocotb":"2.0.1","verilator":"Verilator 5.046 synthetic"}'}
        return json.dumps({"status": "passed", "model_tests": 2, "error_code": None,
            "artifacts": {name: base64.b64encode(data).decode() for name, data in artifacts.items()}}).encode()

    def run_fixture(self) -> tuple[JsonObject, list[list[str]]]:
        selected = profile()
        commands: list[list[str]] = []
        async def process(argv: list[str], config: Path, timeout: int, bound: int = 48*1024*1024) -> tuple[int, bytes]:
            commands.append(argv)
            if "info" in argv: return 0, info()
            if "image" in argv: return 0, ('"' + selected["image_id"] + '"|"linux"|"arm64"').encode()
            if "create" in argv: return 0, b"c"*64
            if "start" in argv: return 0, self.simulation_response()
            if "rm" in argv: return 0, b"c"*64
            raise AssertionError("unexpected process")
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            runtime = IsolatedDesignSimulator(self.root, selected)
            with patch.object(runtime, "_process", side_effect=process):
                files, manifest = collateral(selected)
                report = asyncio.run(runtime.simulate(files, manifest, selftest_digest(selected), "d"*32))
        return report, commands

    def test_selftest_fixture_exercises_real_simulator_guards_and_rehashes_all_evidence(self) -> None:
        report, commands = self.run_fixture()
        verify_evidence(self.root, profile(), "d"*32, report)
        create = next(argv for argv in commands if "create" in argv)
        for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                     "--user=65534:65534", "--cpus=2", "--memory=2048m", "--pids-limit=128"):
            self.assertIn(flag, create)
        self.assertEqual(commands[-1][-3:], ["rm", "--force", "c"*64])
        self.assertNotIn("/unit-only/socket", " ".join(arg for arg in create if arg.startswith("type=bind")))

    def test_guest_transport_probes_exact_mounts_before_design_container(self) -> None:
        class Transport:
            identity = {"schema": "unit-guest-transport", "instance": "unit-only"}
            def __init__(self) -> None:
                self.released: list[str] = []
            async def stage(self, operation_id: str, inputs: Path, control: Path) -> tuple[str, str]:
                self.staged = (operation_id, inputs, control)
                return "/guest/work/input", "/guest/work/control"
            async def release(self, operation_id: str) -> None:
                self.released.append(operation_id)

        transport = Transport()
        commands: list[list[str]] = []
        async def process(argv: list[str], config: Path, timeout: int,
                          bound: int = 48*1024*1024) -> tuple[int, bytes]:
            commands.append(argv)
            if "info" in argv: return 0, info()
            if "image" in argv: return 0, ('"' + profile()["image_id"] + '"|"linux"|"arm64"').encode()
            if "create" in argv:
                return 0, (b"b" if any("-probe" in arg for arg in argv) else b"c") * 64
            if "start" in argv:
                return (0, b"workload-visible\n") if "b"*64 in argv else (0, self.simulation_response())
            if "rm" in argv: return 0, b"removed"
            raise AssertionError("unexpected process")
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            runtime = IsolatedDesignSimulator(self.root, profile(), workload_transport=transport)
            with patch.object(runtime, "_process", side_effect=process):
                files, manifest = collateral(profile())
                report = asyncio.run(runtime.simulate(files, manifest, selftest_digest(profile()), "e"*32))
        self.assertEqual(report["status"], "passed")
        creates = [argv for argv in commands if "create" in argv]
        self.assertEqual(len(creates), 2)
        self.assertIn("type=bind,src=/guest/work/input,dst=/input,readonly", creates[0])
        self.assertIn("--user=65534:65534", creates[0])
        self.assertEqual(transport.released, ["e"*32])
        self.assertEqual(commands.index(creates[1]), commands.index(creates[0]) + 3)

    def test_lima_transport_copies_and_checks_guest_bytes_without_host_permission_repair(self) -> None:
        executable = self.root / "limactl"
        executable.write_bytes(b"unit only")
        executable.chmod(0o700)
        inputs, control = self.root / "input", self.root / "control"
        inputs.mkdir(mode=0o700)
        control.mkdir(mode=0o700)
        (inputs / "rtl.sv").write_bytes(b"module wire; endmodule\n")
        (control / "run.py").write_bytes(b"print('unit')\n")
        transport = LimaWorkloadTransport(executable, self.root, "managed-unit")
        commands: list[list[str]] = []
        async def process(argv: list[str], timeout: int = 30) -> bytes:
            commands.append(argv)
            if "sha256sum" in argv:
                target = inputs / "rtl.sv" if argv[-1].endswith("rtl.sv") else control / "run.py"
                return (hashlib.sha256(target.read_bytes()).hexdigest() + "  " + argv[-1] + "\n").encode()
            return b""
        with patch.object(transport, "_run", side_effect=process):
            paths = asyncio.run(transport.stage("d"*32, inputs, control))
            asyncio.run(transport.release("d"*32))
        self.assertEqual(paths, ("/tmp/openrtl-workloads/" + "d"*32 + "/input",
                                 "/tmp/openrtl-workloads/" + "d"*32 + "/control"))
        self.assertEqual(stat.S_IMODE(inputs.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o700)
        self.assertIn(["copy", "--backend=scp", "--recursive", str(inputs), str(control),
                       "managed-unit:/tmp/openrtl-workloads/" + "d"*32 + "/"], commands)
        self.assertEqual(commands[-1][-4:], ["rm", "-rf", "--", "/tmp/openrtl-workloads/" + "d"*32])

    def test_unreadable_guest_mount_never_starts_design_and_requires_exact_recovery(self) -> None:
        class Transport:
            identity = {"schema": "unit-guest-transport", "instance": "unit-only"}
            def __init__(self) -> None:
                self.released: list[str] = []
            async def stage(self, operation_id: str, inputs: Path, control: Path) -> tuple[str, str]:
                return "/guest/input", "/guest/control"
            async def release(self, operation_id: str) -> None:
                self.released.append(operation_id)
        transport = Transport()
        commands: list[list[str]] = []
        async def process(argv: list[str], config: Path, timeout: int,
                          bound: int = 48*1024*1024) -> tuple[int, bytes]:
            commands.append(argv)
            if "info" in argv: return 0, info()
            if "image" in argv: return 0, ('"' + profile()["image_id"] + '"|"linux"|"arm64"').encode()
            if "create" in argv: return 0, b"b"*64
            if "start" in argv: return 1, b"unreadable fixture"
            if "rm" in argv: return 0, b"removed"
            if "ps" in argv: return 0, b""
            raise AssertionError("unexpected process")
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            runtime = IsolatedDesignSimulator(self.root, profile(), workload_transport=transport)
            with patch.object(runtime, "_process", side_effect=process):
                files, selected_manifest = collateral(profile())
                with self.assertRaisesRegex(ValueError, "guest_workload_visibility_failed"):
                    asyncio.run(runtime.simulate(files, selected_manifest, selftest_digest(profile()), "f"*32))
                self.assertEqual(transport.released, [])
                self.assertTrue((self.root / "runs" / ("f"*32) / "intent.json").is_file())
                asyncio.run(runtime.abandon("f"*32, selftest_digest(profile())))
        self.assertEqual(len([argv for argv in commands if "create" in argv]), 1)
        self.assertEqual(transport.released, ["f"*32])

    def test_changed_input_output_toolchain_and_report_cannot_reuse_selftest(self) -> None:
        report, _ = self.run_fixture()
        for location in ("input/rtl/openrtl_runtime_selftest.sv", "control/run.py", "control/request.json", "evidence/waves.vcd"):
            target = self.root / "runs" / ("d"*32) / location
            original = target.read_bytes()
            target.write_bytes(original + b"changed")
            with self.subTest(location=location), self.assertRaises(ValueError):
                verify_evidence(self.root, profile(), "d"*32, report)
            target.write_bytes(original)
        changed = copy.deepcopy(report)
        changed["runtime"]["profile_digest"] = "sha256:" + "0"*64
        with self.assertRaises(ValueError):
            verify_evidence(self.root, profile(), "d"*32, changed)

    def test_no_create_after_failed_runtime_inspection(self) -> None:
        with patch("openrtl.adapters.runtime_selection.verify_local_identity"):
            runtime = IsolatedDesignSimulator(self.root, profile())
            process = AsyncMock(return_value=(0, info(rootless=False)))
            with patch.object(runtime, "_process", process), self.assertRaises(ValueError):
                files, manifest = collateral(profile())
                asyncio.run(runtime.simulate(files, manifest, selftest_digest(profile()), "e"*32))
        self.assertEqual(process.call_count, 1)
        self.assertFalse(any("create" in call.args[0] for call in process.call_args_list))
        self.assertTrue((self.root / "runs" / ("e"*32) / "intent.json").is_file())

    def test_hash_consistent_but_flat_wave_or_wrong_tool_version_still_fails(self) -> None:
        report, _ = self.run_fixture()
        for name, data in (("waves.vcd", WAVES.replace(b"1!", b"0!")),
                           ("toolchain.json", b'{"cocotb":"2.0.1","verilator":"Verilator 99.999"}')):
            changed = copy.deepcopy(report)
            target = self.root / changed["artifacts"][name]["path"]
            original = target.read_bytes()
            target.write_bytes(data)
            changed["artifacts"][name].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_evidence(self.root, profile(), "d"*32, changed)
            target.write_bytes(original)

    def test_saved_receipt_rechecks_current_source_and_evidence_without_contact(self) -> None:
        report, _ = self.run_fixture()
        receipt = {"schema": "openrtl.runtime-selftest.v1", "attempt": "f"*32, "operation": "d"*32,
                   "profile_digest": content_digest(profile()), "selftest_digest": selftest_digest(profile()),
                   "report_digest": content_digest(report), "status": "passed"}
        with patch("openrtl.runtime_cli._attempt", return_value=self.root), patch("openrtl.runtime_cli.verify_local_identity"), \
                patch("openrtl.runtime_cli.load", side_effect=[profile(), receipt, profile(), receipt]):
            self.assertEqual(ready_profile(self.root), profile())
        receipt["profile_digest"] = "sha256:" + "0"*64
        with patch("openrtl.runtime_cli.load", side_effect=[profile(), receipt]), self.assertRaisesRegex(ValueError, "stale"):
            ready_profile(self.root)


class RuntimeCommandTest(unittest.TestCase):
    def arguments(self, values: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest="command", required=True))
        return parser.parse_args(["runtime", *values])

    def test_plan_has_no_state_inspection_authority_or_processes(self) -> None:
        with patch("openrtl.runtime_cli.load") as load, patch("builtins.print") as emit:
            self.assertEqual(run_runtime_command(self.arguments(["plan", "--json"])), 0)
        load.assert_not_called()
        result = json.loads(emit.call_args.args[0])
        self.assertFalse(result["effects"]["daemon_contact"])
        self.assertFalse(result["m42_complete"])

    def test_mutations_require_consent_before_state_reads_locks_or_processes(self) -> None:
        for command in ("self-test", "recover"):
            with patch("openrtl.runtime_cli.load") as load, patch("openrtl.runtime_cli.writer") as writer, patch("builtins.print") as emit:
                self.assertEqual(run_runtime_command(self.arguments([command])), 2)
            load.assert_not_called()
            writer.assert_not_called()
            self.assertIn("explicit_consent", emit.call_args.args[0])

    def test_legacy_simulation_selection_and_runtime_state_are_exclusive(self) -> None:
        from openrtl.cli import parser
        args = parser().parse_args(["resume", "--project", "/unit-only/project", "--runtime-state", "/unit-only/state"])
        self.assertEqual(args.runtime_state, Path("/unit-only/state"))
        self.assertFalse(args.allow_simulation)

    def test_saved_selection_does_not_create_readiness_without_evidence(self) -> None:
        with patch("openrtl.runtime_cli.load", side_effect=[profile(), None]):
            with self.assertRaisesRegex(ValueError, "selftest_required"):
                ready_profile(Path("/unit-only/state"))

    def test_launcher_state_option_reaches_runtime_but_not_batch(self) -> None:
        from openrtl.onboarding import _forwarded_arguments
        from openrtl.cli import parser
        for prefix in (["--state-dir", "/unit-only/private state"], ["--state-dir=/unit-only/private state"]):
            forwarded = _forwarded_arguments([*prefix, "runtime", "status"])
            self.assertIsNotNone(forwarded)
            assert forwarded is not None
            args = parser().parse_args(forwarded)
            self.assertEqual(args.state_dir, Path("/unit-only/private state"))
            self.assertEqual(_forwarded_arguments([*prefix, "batch", "--help"]), ["batch", "--help"])


class RuntimeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # This unit isolates atomic-file/locking behavior from the already tested
        # onboarding ancestor walker; production always uses the actual walker.
        self.walk = patch("openrtl.adapters.runtime_store._open_state",
                          side_effect=lambda path, create: os.open(self.root, os.O_RDONLY | os.O_DIRECTORY))
        self.walk.start()
        self.addCleanup(self.walk.stop)

    def test_atomic_roundtrip_interruption_and_unknown_names(self) -> None:
        runtime_store.save(self.root, "runtime.json", profile())
        self.assertEqual(runtime_store.load(self.root, "runtime.json"), profile())
        original = (self.root / "runtime.json").read_bytes()
        with patch("openrtl.adapters.runtime_store.os.replace", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                runtime_store.save(self.root, "runtime.json", {**profile(), "daemon_id": "changed"})
        self.assertEqual((self.root / "runtime.json").read_bytes(), original)
        self.assertFalse(list(self.root.glob(".runtime-*.tmp")))
        with self.assertRaises(ValueError): runtime_store.load(self.root, "../elsewhere")

    def test_concurrent_writer_refuses_and_releases_after_interruption(self) -> None:
        with runtime_store.writer(self.root):
            with self.assertRaisesRegex(ValueError, "writer_active"):
                with runtime_store.writer(self.root): pass
        with self.assertRaises(RuntimeError):
            with runtime_store.writer(self.root): raise RuntimeError("synthetic interruption")
        with runtime_store.writer(self.root): pass

    def test_links_and_unowned_permissions_are_not_replaced(self) -> None:
        target = self.root / "unrelated"
        target.write_bytes(b"preserve")
        (self.root / "runtime.json").symlink_to(target)
        with self.assertRaises((ValueError, OSError)): runtime_store.save(self.root, "runtime.json", profile())
        self.assertEqual(target.read_bytes(), b"preserve")
        with self.assertRaises((ValueError, OSError)): runtime_store.load(self.root, "runtime.json")


class RuntimeRecoveryTest(RuntimeTemporaryTest):
    def test_uncertain_selftest_is_not_replayed_and_selection_is_not_replaced(self) -> None:
        from openrtl.runtime_cli import _no_active_receipt
        for phase in ("running", "needs-recovery"):
            receipt = {"schema": "openrtl.runtime-selftest.v1", "attempt": "a"*32, "operation": "b"*32,
                       "profile_digest": content_digest(profile()), "selftest_digest": selftest_digest(profile()), "status": phase}
            with patch("openrtl.runtime_cli.load", return_value=receipt):
                with self.assertRaisesRegex(ValueError, "recovery_required"):
                    _no_active_receipt(self.root)

    def test_recovery_does_not_guess_when_intent_is_missing(self) -> None:
        from openrtl.runtime_cli import _recover
        receipt = {"schema": "openrtl.runtime-selftest.v1", "attempt": "a"*32, "operation": "b"*32,
                   "profile_digest": content_digest(profile()), "selftest_digest": selftest_digest(profile()),
                   "status": "needs-recovery"}
        with patch("openrtl.runtime_cli.load", return_value=receipt), patch("openrtl.runtime_cli._profile", return_value=profile()), \
                patch("openrtl.runtime_cli.IsolatedDesignSimulator") as simulator, patch("openrtl.runtime_cli.save") as save:
            with self.assertRaisesRegex(ValueError, "manual_reconciliation"):
                asyncio.run(_recover(argparse.Namespace(allow_runtime_contact=True), self.root))
        simulator.assert_not_called()
        save.assert_not_called()

    def test_interrupted_selftest_retains_uncertainty_and_original_exception(self) -> None:
        from openrtl.runtime_cli import _selftest
        recorded: list[JsonObject] = []
        def save(state: Path, name: str, value: JsonObject) -> None:
            recorded.append(dict(value))
        with patch("openrtl.runtime_cli.load", return_value=None), patch("openrtl.runtime_cli._profile", return_value=profile()), \
                patch("openrtl.runtime_cli._new_attempt", return_value=self.root), \
                patch("openrtl.runtime_cli.save", side_effect=save), patch("openrtl.runtime_cli.IsolatedDesignSimulator") as simulator:
            simulator.return_value.simulate = AsyncMock(side_effect=asyncio.CancelledError())
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(_selftest(argparse.Namespace(allow_runtime_contact=True), self.root))
        self.assertEqual([row["status"] for row in recorded], ["not-started", "running", "needs-recovery"])


if __name__ == "__main__":
    unittest.main()
