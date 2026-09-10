"""Artifact audit composition; synthetic bytes never qualify a guest runtime."""

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.capabilities import BackendArtifactAudit
from openrtl.adapters.backend_setup import audit_backend_bundle
from openrtl.runtime_cli import add_runtime_command, run_runtime_command


def manifest(payload: bytes = b'synthetic fixture') -> str:
    return json.dumps({'schema': 'agentrig.backend-artifacts.v1', 'artifacts': [
        {'id': 'guest', 'path': 'guest.raw', 'sha256': 'sha256:' + hashlib.sha256(payload).hexdigest(),
         'size_bytes': len(payload), 'executable': False}]})


class BackendArtifactTests(unittest.TestCase):
    def arguments(self, values: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_runtime_command(parser.add_subparsers(dest='command', required=True))
        return parser.parse_args(['runtime', *values])

    def test_actual_private_bundle_read_leaves_no_state_and_no_runtime_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix='openrtl-artifact-audit-') as temporary:
            root = Path(temporary).resolve()
            payload = b'synthetic fixture'
            path = root / 'guest.raw'
            path.write_bytes(payload)
            path.chmod(0o600)
            before = path.stat()
            with redirect_stdout(io.StringIO()) as output:
                code = run_runtime_command(self.arguments(['backend-artifacts', '--artifact-root', str(root),
                                                          '--manifest-json', manifest(payload), '--json']))
            self.assertEqual(code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertTrue(result['declared_bytes_verified'])
            self.assertFalse(result['ready_for_simulation'])
            self.assertFalse(result['dependency_closure_qualified'])
            self.assertEqual([p.name for p in root.iterdir()], ['guest.raw'])
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_invalid_manifest_is_rejected_before_filesystem_access(self) -> None:
        cases = ['{}', '{"schema":"x","schema":"y","artifacts":[]}', manifest().replace('guest.raw', '../escape'),
                 manifest().replace('guest.raw', '.env'), manifest().replace('"size_bytes": 17', '"size_bytes": true')]
        with patch('openrtl.onboarding._open_state') as opened:
            for payload in cases:
                with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, '^runtime_backend_artifacts_invalid$'):
                    audit_backend_bundle(Path('/private/fixture'), payload)
            opened.assert_not_called()

    def test_composition_never_turns_a_byte_audit_into_readiness(self) -> None:
        audit = BackendArtifactAudit(manifest_digest='sha256:' + '1' * 64, file_count=1, total_bytes=17)
        with patch('openrtl.onboarding._open_state', return_value=91) as opened, \
                patch('openrtl.adapters.backend_setup.os.close'), \
                patch('agentrig.integrations.backend_artifacts.audit_backend_artifacts', return_value=audit) as verify:
            result = audit_backend_bundle(Path('/private/fixture'), manifest())
        self.assertEqual(opened.call_args.kwargs, {'create': False})
        self.assertEqual(verify.call_args.args[0], Path('/private/fixture'))
        self.assertIsNotNone(verify.call_args.args[2].deadline)
        for key in ('dependency_closure_qualified', 'execution_authorized', 'runtime_contact',
                    'ready_for_simulation', 'installation', 'selection_changed'):
            self.assertFalse(result[key])
        self.assertNotIn('/private/fixture', json.dumps(result))

    def test_cli_audit_does_not_open_runtime_store(self) -> None:
        with patch('openrtl.adapters.backend_setup.audit_backend_bundle', return_value={}) as audit, \
                patch('openrtl.runtime_cli.writer') as writer, redirect_stdout(io.StringIO()):
            code = run_runtime_command(self.arguments(['backend-artifacts', '--artifact-root', '/private/fixture',
                                                       '--manifest-json', manifest()]))
        self.assertEqual(code, 0)
        audit.assert_called_once_with(Path('/private/fixture'), manifest())
        writer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
