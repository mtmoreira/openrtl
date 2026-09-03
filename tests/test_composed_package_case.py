"""Synthetic verifier tests; real simulation is the explicit example lane."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import Mock

from openrtl.adapters import DependencyClosedCatalog
from tools.composed_package_case import (
    COUNTERS, PACKAGE_IDS, SOURCE_PATHS, consumer_sources, read_file, run_bounded, verify_run,
)


class ComposedPackageCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'run.log').write_text('TESTS=1 PASS=1 FAIL=0 SKIP=0\n')
        (self.root / 'results.xml').write_text(
            '<testsuites><testsuite><testcase name="composed_stream_contract" /></testsuite></testsuites>\n')
        (self.root / 'waves.vcd').write_text('$timescale 1ns $end\n$enddefinitions $end\n')
        self.coverage: dict[str, Any] = {'schema': 'openrtl.composed-stream-coverage.v1', 'seed': 33,
                         'status': 'passed', 'drained': True,
                         'counts': {name: 5 if name == 'max_occupancy' else 1 for name in COUNTERS}}
        self._coverage()

    def _coverage(self) -> None:
        (self.root / 'coverage.json').write_text(json.dumps(self.coverage))

    def test_passing_artifact_contract(self) -> None:
        self.assertEqual(verify_run(self.root), self.coverage)

    def test_missing_coverage_and_wrong_seed_fail(self) -> None:
        self.coverage['seed'] = 34
        self._coverage()
        with self.assertRaisesRegex(ValueError, 'identity'):
            verify_run(self.root)
        self.coverage['seed'] = 33
        self.coverage['counts'] = {name: 0 for name in COUNTERS}
        self._coverage()
        with self.assertRaisesRegex(ValueError, 'coverage missing'):
            verify_run(self.root)

    def test_failed_skipped_or_wrong_testcase_fails(self) -> None:
        for contents in ('<testcase name="other"/>',
                         '<testcase name="composed_stream_contract"><failure/></testcase>',
                         '<testcase name="composed_stream_contract"><skipped/></testcase>'):
            with self.subTest(contents=contents):
                (self.root / 'results.xml').write_text(f'<testsuites>{contents}</testsuites>')
                with self.assertRaisesRegex(ValueError, 'testcase'):
                    verify_run(self.root)

    def test_warnings_symlinks_and_size_bounds_fail(self) -> None:
        (self.root / 'run.log').write_text('%Warning-WIDTH\nTESTS=1 PASS=1 FAIL=0 SKIP=0\n')
        with self.assertRaisesRegex(ValueError, 'warning'):
            verify_run(self.root)
        with self.assertRaisesRegex(ValueError, 'size'):
            read_file(self.root / 'run.log', 1)
        link = self.root / 'link'
        link.symlink_to(self.root / 'run.log')
        with self.assertRaisesRegex(ValueError, 'non-symlink'):
            read_file(link)

    def test_materialized_sources_rehashed_before_compilation(self) -> None:
        catalog = Mock()
        pins = [SimpleNamespace(package_id=name, version='1.0.0', manifest_digest='pin') for name in PACKAGE_IDS]
        catalog.read_lock.return_value = SimpleNamespace(packages=pins)
        packages = []
        for package_id, relative in zip(PACKAGE_IDS, SOURCE_PATHS, strict=True):
            target = self.root / 'packages' / package_id / relative
            target.parent.mkdir(parents=True)
            target.write_text('module fixture; endmodule\n')
            digest = 'sha256:' + hashlib.sha256(target.read_bytes()).hexdigest()
            packages.append(SimpleNamespace(package=SimpleNamespace(package_id=package_id,
                            files=[SimpleNamespace(path=relative, content_digest=digest)])))
        catalog.portable.read_package.side_effect = packages
        sources = consumer_sources(self.root, cast(DependencyClosedCatalog, catalog), self.root / 'lock', 'digest')
        self.assertEqual(len(sources), 3)
        sources[0].write_text('tampered\n')
        catalog.portable.read_package.side_effect = packages
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            consumer_sources(self.root, cast(DependencyClosedCatalog, catalog), self.root / 'lock', 'digest')

    def test_process_failure_and_deadline(self) -> None:
        output = self.root / 'failed'
        output.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'simulation failed'):
            run_bounded([sys.executable, '-I', '-c', 'raise SystemExit(3)'], self.root, {}, output, 5)
        output = self.root / 'timeout'
        output.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            run_bounded([sys.executable, '-I', '-c', 'import time; time.sleep(10)'], self.root, {}, output, 1)


if __name__ == '__main__':
    unittest.main()
