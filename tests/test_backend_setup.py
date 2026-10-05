"""Offline setup review and backend substitution; no runtime qualification."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agentrig.capabilities.local_backend import LocalBackendCatalog
from agentrig.capabilities import BackendAction, BackendOperation
from agentrig.integrations import LocalBackendJournal
from agentrig.testing import ScriptedLocalBackend
from openrtl.adapters.backend_setup import backends, review_backend, backend_operation_status
from openrtl.runtime_cli import add_runtime_command, run_runtime_command


CONFIG = json.dumps({"host": "darwin", "architecture": "arm64", "state_root": "/private/fixture",
                     "cpus": 2, "memory_mib": 2048, "disk_gib": 20})
OPERATION = "a" * 32


class BackendSetupTests(unittest.TestCase):
    def test_actual_operation_command_reads_private_journal_without_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openrtl-backend-status-') as temporary:
            state = Path(temporary).resolve()
            directory = state / 'backend-operations'
            directory.mkdir(mode=0o700)
            record = BackendOperation(operation_id='a' * 32, backend_id='synthetic', backend_version='1',
                configuration_digest='sha256:' + '1' * 64, plan_digest='sha256:' + '2' * 64,
                action=BackendAction.START)
            with LocalBackendJournal(directory).locked() as journal:
                journal.create(record)
            before = {item.name: item.read_bytes() for item in directory.iterdir()}
            with redirect_stdout(io.StringIO()) as output:
                code = run_runtime_command(self.arguments(['backend-operation', '--state-dir', str(state), '--json']))
            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'uncertain')
            self.assertFalse(result['ready_for_simulation'])
            self.assertEqual(before, {item.name: item.read_bytes() for item in directory.iterdir()})

    def test_absent_operation_status_does_not_create_state(self) -> None:
        with patch('openrtl.onboarding._open_state', side_effect=FileNotFoundError) as open_state:
            result = backend_operation_status(Path('/private/fixture'))
        self.assertEqual(result['status'], 'unconfigured')
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(open_state.call_args.kwargs, {'create': False})

    def test_operation_status_is_diagnostic_without_restored_authority(self) -> None:
        record = BackendOperation(operation_id='a' * 32, backend_id='synthetic', backend_version='1',
            configuration_digest='sha256:' + '1' * 64, plan_digest='sha256:' + '2' * 64,
            action=BackendAction.START)
        with patch('openrtl.onboarding._open_state', return_value=91), \
                patch('openrtl.adapters.backend_setup.os.close'), \
                patch('agentrig.integrations.backend_journal.LocalBackendJournal.inspect', return_value=record):
            result = backend_operation_status(Path('/private/fixture'))
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse(result['ready_for_simulation'])
        self.assertFalse(result['runtime_contact'])
        self.assertNotIn('configuration_digest', result)

    def test_operation_cli_preserves_explicit_state_directory(self) -> None:
        with patch('openrtl.adapters.backend_setup.backend_operation_status', return_value={}) as inspect, \
                redirect_stdout(io.StringIO()):
            code = run_runtime_command(self.arguments(['backend-operation', '--state-dir', '/private/fixture']))
        self.assertEqual(code, 0)
        inspect.assert_called_once_with(Path('/private/fixture'))

    def arguments(self, values: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest="command", required=True))
        return parser.parse_args(["runtime", *values])

    def test_lima_review_is_blocked_and_never_selection(self) -> None:
        result = review_backend("lima-vz", "prepare", CONFIG, OPERATION)
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["ready_for_simulation"])
        self.assertFalse(result["selection_changed"])
        self.assertIn("guest_image_pin_required", result["plan"]["blockers"])
        self.assertEqual(result["m46"], "pending")
        self.assertEqual(result["m47"], "pending")
        self.assertEqual(result, review_backend("lima-vz", "prepare", CONFIG, OPERATION))

    def test_alternative_backend_uses_identical_review_consumer(self) -> None:
        backend = ScriptedLocalBackend(["running"])
        catalog = LocalBackendCatalog([backend])
        result = review_backend("scripted-local", "inspect", '{"fixture":true}', OPERATION, catalog)
        self.assertEqual(result["plan"]["backend_id"], "scripted-local")
        self.assertEqual(result["plan"]["blockers"], [])
        self.assertFalse(result["ready_for_simulation"])
        self.assertEqual(backend.calls, ())
        self.assertEqual(backends(catalog)["backends"][0]["id"], "scripted-local")

    def test_backend_cli_does_not_load_or_mutate_state(self) -> None:
        for values in (["backends", "--json"], ["backend-plan", "--backend", "lima-vz",
            "--action", "prepare", "--config-json", CONFIG, "--operation-id", OPERATION, "--json"]):
            with self.subTest(command=values[0]), patch("openrtl.runtime_cli.load") as load, \
                    patch("openrtl.runtime_cli.writer") as writer, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_runtime_command(self.arguments(list(values))), 0)
                result = json.loads(output.getvalue())
                self.assertFalse(result["runtime_contact"])
                load.assert_not_called()
                writer.assert_not_called()

    def test_unknown_backend_has_no_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_backend_unavailable"):
            review_backend("unknown", "prepare", CONFIG, OPERATION)

    def test_invalid_config_is_bounded_and_redacted(self) -> None:
        for config in ('{"secret":"private-value",}', '{"host":"darwin","host":"linux"}',
                       '[]', '{"private":"' + "x" * 65536 + '"}', '{"nan":NaN}'):
            with self.subTest(size=len(config)), self.assertRaises(ValueError) as caught:
                review_backend("lima-vz", "prepare", config, OPERATION)
            self.assertEqual(str(caught.exception), "runtime_backend_configuration_invalid")

    def test_new_commands_fail_cleanly_with_published_sdk(self) -> None:
        with patch.dict(sys.modules, {"agentrig.capabilities.local_backend": None}), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_runtime_command(self.arguments(["backends", "--json"])), 2)
        self.assertIn("runtime_backend_sdk_candidate_required", output.getvalue())

    def test_original_runtime_plan_does_not_need_candidate_sdk(self) -> None:
        with patch.dict(sys.modules, {"agentrig.capabilities.local_backend": None}), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_runtime_command(self.arguments(["plan", "--json"])), 0)
        self.assertEqual(json.loads(output.getvalue())["m46"], "pending")

    def test_operation_and_capability_changes_invalidate_plan_digest(self) -> None:
        first = review_backend("lima-vz", "prepare", CONFIG, OPERATION)
        second = review_backend("lima-vz", "prepare", CONFIG, "b" * 32)
        changed = json.loads(CONFIG)
        changed["cpus"] = 4
        third = review_backend("lima-vz", "prepare", json.dumps(changed), OPERATION)
        self.assertEqual(len({first["plan_digest"], second["plan_digest"], third["plan_digest"]}), 3)


if __name__ == "__main__":
    unittest.main()
