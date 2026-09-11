"""Configuration audit stays outside engineering state and runtime selection."""

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.backend_setup import audit_backend_configuration
from openrtl.runtime_cli import add_runtime_command, run_runtime_command
from openrtl.domain.design_session import JsonObject


class BackendConfigurationTests(unittest.TestCase):
    def arguments(self, argv: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest='command', required=True))
        return parser.parse_args(['runtime', *argv])

    def test_replacement_auditor_and_no_restored_readiness(self) -> None:
        calls: list[JsonObject] = []
        def other(policy: JsonObject) -> JsonObject:
            calls.append(policy)
            return {'policy_digest': 'synthetic', 'ready_for_simulation': True, 'private_detail': 'hidden'}
        result = audit_backend_configuration('other', '{"example":1}', {'other': other})
        self.assertEqual(calls, [{'example': 1}])
        self.assertFalse(result['ready_for_simulation'])
        self.assertEqual(result['m46'], 'pending')
        self.assertNotIn('private_detail', result)

    def test_strict_json_unknown_backend_and_private_error(self) -> None:
        for text in ['[]', '{"a":1,"a":2}', '{', ' ' * 65537]:
            with self.assertRaisesRegex(ValueError, '^runtime_backend_config_audit_invalid$'):
                audit_backend_configuration('other', text, {'other': lambda p: {}})
        with self.assertRaisesRegex(ValueError, '^runtime_backend_config_audit_invalid$'):
            audit_backend_configuration('missing', '{}', {})
        for failure in (ValueError, RuntimeError):
            def broken(policy: JsonObject) -> JsonObject:
                raise failure('private adapter diagnostic')
            with self.assertRaisesRegex(ValueError, '^runtime_backend_config_audit_invalid$'):
                audit_backend_configuration('other', '{}', {'other': broken})

    def test_cli_routes_before_selection_writer(self) -> None:
        result = audit_backend_configuration('other', '{}', {'other': lambda p: {}})
        with (patch('openrtl.adapters.backend_setup.audit_backend_configuration', return_value=result) as audit,
                patch('openrtl.runtime_cli.writer', side_effect=AssertionError('must not write'))):
            output = io.StringIO()
            with redirect_stdout(output):
                code = run_runtime_command(self.arguments(['backend-config', '--backend', 'other', '--policy-json', '{}', '--json']))
        self.assertEqual(code, 0)
        audit.assert_called_once_with('other', '{}')
        self.assertFalse(json.loads(output.getvalue())['selection_changed'])

    def test_actual_private_configuration_audit(self) -> None:
        from agentrig.integrations.lima_configuration import LimaConfiguration
        with tempfile.TemporaryDirectory(prefix='oc-', dir='/tmp') as directory:
            root = Path(directory).resolve()
            data = b'synthetic image, not bootable'
            pin = 'sha256:' + hashlib.sha256(data).hexdigest()
            configuration = LimaConfiguration(state_root=root, guest_image_sha256=pin, guest_image_size=len(data))
            (root / 'configuration.json').write_bytes(configuration.json_bytes())
            (root / 'configuration.json').chmod(0o600)
            (root / 'artifacts').mkdir(mode=0o700)
            (root / 'artifacts/guest.raw').write_bytes(data)
            (root / 'artifacts/guest.raw').chmod(0o600)
            policy = json.dumps({'state_root': str(root), 'guest_image_sha256': pin, 'guest_image_size': len(data)})
            result = audit_backend_configuration('lima-vz', policy)
            self.assertTrue(result['input_configuration_verified'])
            self.assertFalse(result['effective_configuration_verified'])
            self.assertFalse(result['dependency_closure_qualified'])


if __name__ == '__main__':
    unittest.main()
