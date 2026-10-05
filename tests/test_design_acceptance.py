"""Acceptance contracts with explicitly manufactured receipts, not live evidence."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_acceptance import acceptance_report
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import design_input_digest
from openrtl.design_cli import add_design_commands, run_design_command
from openrtl.domain.design_session import content_digest, canonical, validate_spec
from openrtl.domain.design_delegation import warning
from tests.test_design_agent import specification
from tests import test_design_coaching as coaching_fixture
from tools.verify_design_agent_install import CASES, GUIDE_FILES, build_evaluation_archive, package_digest, verify_install


class AcceptanceReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = coaching_fixture.DesignCoachingTest()
        self.fixture.setUp()
        self.store, self.agent = self.fixture.store, self.fixture.agent

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def ready(self) -> None:
        asyncio.run(self.agent.advance())
        self.fixture.recorded_run()
        asyncio.run(self.agent.advance())
        state = self.store.read()
        self.agent.approve(content_digest({"input": design_input_digest(state), "simulation": state["simulation"],
                                          "review": state["review"]}))

    def test_manufactured_consistent_receipt_is_never_live_qualification(self) -> None:
        self.ready()
        before = self.store.read()
        database = self.store.root / "session.sqlite3"
        before_bytes = database.read_bytes()
        report = acceptance_report(self.store, specification())
        self.assertEqual(report["status"], "local_gates_satisfied")
        self.assertEqual(report["evidence_tier"], "reverified_local_receipt")
        self.assertEqual(report["live_qualification"], "not_established_by_this_report")
        self.assertTrue(report["expected_spec_checked"])
        self.assertFalse(any(report["effects"].values()))
        self.assertEqual(report["requirement_tests"], before["manifest"]["requirement_tests"])
        digest = report.pop("content_digest")
        self.assertEqual(digest, content_digest(report))
        self.assertEqual(self.store.read(), before)
        self.assertEqual(database.read_bytes(), before_bytes)

    def test_unexecuted_collateral_is_pending(self) -> None:
        report = acceptance_report(self.store)
        self.assertEqual(report["status"], "pending")
        self.assertIn("passing_simulation_missing", report["blockers"])
        self.assertIsNone(report["measurement"])

    def test_empty_project_reports_missing_gates_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "session", create=True)
            try:
                report = acceptance_report(store)
                self.assertIn("requirements_not_approved", report["blockers"])
                self.assertIn("engineering_collateral_incomplete", report["blockers"])
                self.assertEqual(store.read()["revision"], 0)
            finally:
                store.close()

    def test_spec_drift_rejected(self) -> None:
        changed = specification()
        changed["requirements"][0]["text"] = "A different requirement"
        with self.assertRaisesRegex(ValueError, "differs_from_review"):
            acceptance_report(self.store, changed)

    def test_legacy_simulation_does_not_satisfy_runtime_evidence(self) -> None:
        asyncio.run(self.agent.advance())
        report = acceptance_report(self.store)
        self.assertIn("fresh_runtime_bound_simulation_required", report["blockers"])

    def test_changed_evidence_is_hard_failure_not_pending(self) -> None:
        self.ready()
        simulation = self.store.read()["simulation"]
        (self.store.root / simulation["artifacts"]["results.xml"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "measurement_artifact_changed"):
            acceptance_report(self.store)

    def test_model_count_is_checked_even_when_hashes_match(self) -> None:
        self.ready()
        previous = self.store.read()
        updated = copy.deepcopy(previous)
        updated["simulation"]["model_tests"] = 500
        run = self.store.root / "runs" / updated["simulation"]["run_id"]
        (run / "evidence/report.json").write_bytes(canonical(updated["simulation"]))
        self.store.save(previous, updated, "simulation.completed")
        with self.assertRaisesRegex(ValueError, "model_tests_invalid"):
            acceptance_report(self.store)

    def test_source_blob_tampering_rejected_before_summary(self) -> None:
        self.store.connection.execute("UPDATE blobs SET content=?", (b"tampered",))
        with self.assertRaisesRegex(ValueError, "artifact_bytes_changed"):
            acceptance_report(self.store)

    def test_signoff_is_not_final_user_acceptance(self) -> None:
        asyncio.run(self.agent.advance())
        self.fixture.recorded_run()
        asyncio.run(self.agent.advance())
        report = acceptance_report(self.store)
        self.assertEqual(report["blockers"], ["final_acceptance_missing"])

    def test_pending_warning_keeps_local_gates_open(self) -> None:
        self.ready()
        previous = self.store.read()
        updated = copy.deepcopy(previous)
        row = warning("assumption", "fixture.choice", previous["approved_spec"],
                      "Scripted assumption warning", "Requires explicit user review")
        updated["warnings"].append(row)
        self.store.save(previous, updated, "spec.proposed")
        report = acceptance_report(self.store)
        self.assertIn("warnings_require_user_review", report["blockers"])
        self.assertEqual(report["pending_warning_ids"], [row["id"]])
        self.agent.acknowledge_warning(row["id"])
        self.assertEqual(acceptance_report(self.store)["status"], "local_gates_satisfied")

    def test_cli_read_only_pending_exit_is_two(self) -> None:
        parser = argparse.ArgumentParser()
        add_design_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["acceptance", "--project", str(self.store.root)])
        self.assertFalse(hasattr(args, "allow_provider"))
        self.assertFalse(hasattr(args, "allow_simulation"))
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(run_design_command(args), 2)
        self.assertEqual(json.loads(stream.getvalue())["status"], "pending")


class InstalledAgentContractTest(unittest.TestCase):
    def test_evaluation_archive_is_exact_reproducible_and_nonoverwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in GUIDE_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fixture " + name + "\n")
            first = build_evaluation_archive(root, root / "first.tar.gz")
            second = build_evaluation_archive(root, root / "second.tar.gz")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual(archive.getnames(), ["openrtl-design-agent-evaluation/" + name for name in GUIDE_FILES])
                self.assertTrue(all(m.isfile() and not m.issym() and not m.islnk() for m in archive.getmembers()))
            with self.assertRaisesRegex(ValueError, "must_be_new"):
                build_evaluation_archive(root, first)

    def test_acceptance_inputs_are_distinct_complete_specs_not_solutions(self) -> None:
        root = Path(__file__).resolve().parents[1] / "examples/design_acceptance"
        tops = set()
        for filename in CASES:
            spec = validate_spec(json.loads((root / filename).read_bytes()))
            tops.add(spec["top"])
            self.assertEqual(spec["questions"], [])
            self.assertEqual(spec["assumptions"], [])
            self.assertTrue(spec["ports"])
        self.assertEqual(len(tops), 3)
        self.assertEqual({p.suffix for p in root.iterdir()}, {".json", ".md"})

    def test_package_mismatch_fails_before_cli_or_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "output"
            with patch("tools.verify_design_agent_install.package_digest", return_value="sha256:" + "a" * 64), \
                    patch("tools.verify_design_agent_install.subprocess.run") as execute:
                with self.assertRaisesRegex(ValueError, "package_bytes_differ"):
                    verify_install(root / "installed", output, root / "specs", "sha256:" + "b" * 64)
                execute.assert_not_called()
            self.assertFalse(output.exists())

    def test_package_name_preflight_rejects_secret_before_payload_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / "openrtl"
            package.mkdir()
            (package / ".env").touch()  # Empty forbidden-name fixture, no secret value.
            with patch.object(Path, "read_bytes") as read:
                with self.assertRaisesRegex(ValueError, "hidden_package_payload"):
                    package_digest(root)
                read.assert_not_called()

    def test_package_digest_is_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / "openrtl"
            (package / "adapters").mkdir(parents=True)
            for name in ("cli.py", "adapters/_design_runner.py", "adapters/design_acceptance.py"):
                (package / name).write_bytes(b"# Package-only fixture.\n")
            first = package_digest(root)
            (package / "cli.py").write_bytes(b"# Changed fixture.\n")
            self.assertNotEqual(first, package_digest(root))


if __name__ == "__main__":
    unittest.main()
