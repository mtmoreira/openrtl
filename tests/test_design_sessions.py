"""Synthetic session portability and diagnostics; no real provider/runtime calls."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_portable import (
    document, portable_input, portable_material, restore_portable, restored_material, validate_records, write_portable,
)
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_sessions import discover, inspect_session, support_bundle
from openrtl.application.design_agent import DesignAgent, ExpertReply
from openrtl.application.design_diagnostics import diagnostic, observed
from openrtl.cli import main
from openrtl.domain.design_session import JsonObject, canonical, content_digest
from tests.test_design_agent import FakeExpert
from tests.test_design_conversation import ready_spec


class SessionServicesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.projects = self.root / "projects"; self.projects.mkdir()
        self.store = DesignSessionStore(self.projects / "first design", create=True)
        self.addCleanup(self.store.close)
        self.agent = DesignAgent(self.store, FakeExpert())
        self.agent.propose(ready_spec())

    def backup(self, name: str = "backup") -> tuple[Path, JsonObject]:
        plan, _ = portable_material(self.store)
        destination = self.root / name
        write_portable(self.store, destination, content_digest(plan))
        return destination, plan

    def input_fixture(self, name: str) -> tuple[Path, JsonObject]:
        # Test-only construction isolates input validation from output publication.
        plan, files = portable_material(self.store)
        root = self.root / name; root.mkdir()
        for path, data in files.items():
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        (root / "openrtl-session.json").write_bytes(canonical(plan) + b"\n")
        return root, plan

    def generate(self) -> None:
        self.agent.approve(content_digest(self.store.read()["spec"]))
        asyncio.run(self.agent.advance())

    def test_readonly_discovery_and_inspection_with_writer_active(self) -> None:
        before = self.store.read()
        readonly = DesignSessionStore(self.store.root, read_only=True)
        try:
            info = inspect_session(readonly)
            self.assertFalse(info["runtime_authority"])
            self.assertEqual(info["revision"], before["revision"])
            rows = discover(self.projects)["sessions"]
            self.assertEqual(rows[0]["name"], "first design")
        finally: readonly.close()
        self.assertEqual(self.store.read(), before)

    def test_listing_pagination_filters_links_and_bounds(self) -> None:
        (self.projects / "0-link").symlink_to(self.store.root, target_is_directory=True)
        self.assertEqual(discover(self.projects, limit=1)["sessions"], [])
        self.assertEqual(discover(self.projects, offset=1, limit=1)["sessions"][0]["name"], "first design")
        with self.assertRaises(ValueError): discover(self.projects, limit=0)
        with self.assertRaises(ValueError): discover(self.projects, offset=-1)

    def test_corrupt_or_incomplete_session_has_safe_listing_error(self) -> None:
        bad = self.projects / "bad"; bad.mkdir()
        (bad / "session.sqlite3").write_bytes(b"SYNTHETIC_PRIVATE_PAYLOAD")
        result = discover(self.projects)
        self.assertEqual(result["sessions"][0]["status"], "unavailable")
        self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", json.dumps(result))
        (self.store.root / "INCOMPLETE").write_text("interrupted")
        with self.assertRaisesRegex(ValueError, "incomplete"): DesignSessionStore(self.store.root, read_only=True)

    def test_hardlinked_database_is_refused(self) -> None:
        os.link(self.store.root / "session.sqlite3", self.root / "alias.db")
        with self.assertRaisesRegex(ValueError, "storage"): DesignSessionStore(self.store.root, read_only=True)

    def test_portable_material_is_readonly_and_keeps_history_blobs(self) -> None:
        self.generate()
        before = self.store.read()
        plan, files = portable_material(self.store)
        states = validate_records(document(files["records.json"]), files)
        self.assertEqual(states[-1], before)
        self.assertEqual(len(states), before["revision"] + 1)
        self.assertEqual(len([p for p in files if p.startswith("blobs/")]), 1)
        self.assertTrue(plan["source_containing"])
        self.assertFalse(plan["runtime_authority"])
        self.assertEqual(self.store.read(), before)

    def test_roundtrip_retains_history_calls_limits_warnings_and_revokes_permissions(self) -> None:
        self.generate()
        before = self.store.read(); source, plan = self.backup()
        target = self.root / "restored session"
        restore_portable(source, target, content_digest(plan))
        restored = DesignSessionStore(target)
        try:
            state = restored.read()
            self.assertEqual({**state, "revision": before["revision"]}, before)
            for revision in range(before["revision"] + 1):
                self.assertEqual(restored.historical_state(revision), self.store.historical_state(revision))
            self.assertEqual(restored.contents(state), self.store.contents(before))
            self.assertEqual(restored.events()[-1]["event"], "session.restored")
            self.assertFalse(restored.operation_owned("a" * 32))
            with self.assertRaisesRegex(ValueError, "expert_not_configured"): asyncio.run(DesignAgent(restored).advance())
        finally: restored.close()
        self.assertEqual(self.store.read(), before)

    def test_changed_or_existing_destination_never_overwrites(self) -> None:
        plan, _ = portable_material(self.store)
        self.agent.detail("brief")
        target = self.root / "new"
        with self.assertRaisesRegex(ValueError, "stale"): write_portable(self.store, target, content_digest(plan))
        self.assertFalse(target.exists())
        source, plan = self.backup()
        target.mkdir()
        with self.assertRaisesRegex(ValueError, "new"): restore_portable(source, target, content_digest(plan))

    def test_active_operations_block_export_without_refund_or_replay(self) -> None:
        before = self.store.read(); updated = copy.deepcopy(before)
        updated.update(calls=1, active={"id": "b" * 32, "kind": "expert", "stage": "discovery"})
        self.store.save(before, updated, "operation.started", {"operation_id": "b" * 32})
        with self.assertRaisesRegex(ValueError, "reconciled"): portable_material(self.store)
        self.assertEqual(self.store.read()["calls"], 1)
        self.assertEqual(inspect_session(self.store)["active"]["id"], "b" * 32)

    def test_recovery_warning_and_budget_survive_portable_roundtrip(self) -> None:
        before = self.store.read(); updated = copy.deepcopy(before)
        updated.update(calls=1, active={"id": "c" * 32, "kind": "expert", "stage": "discovery"})
        self.store.save(before, updated, "operation.started", {"operation_id": "c" * 32})
        # Reopening is required; a current writer cannot abandon its owned invocation.
        with self.assertRaisesRegex(ValueError, "current_writer"): asyncio.run(self.agent.abandon("c" * 32))
        self.store._owned_operations.clear()  # Fixture models a fresh process, not runtime cleanup.
        asyncio.run(self.agent.abandon("c" * 32))
        source, plan = self.backup(); destination = self.root / "recovered"
        restore_portable(source, destination, content_digest(plan))
        restored = DesignSessionStore(destination, read_only=True)
        try:
            self.assertEqual(restored.read()["calls"], 1)
            self.assertEqual(restored.read()["warnings"], self.store.read()["warnings"])
            self.assertEqual(inspect_session(restored)["usage"]["input_tokens"]["unknown_calls"], 1)
        finally: restored.close()

    def test_missing_historical_blob_refuses_even_after_current_files_removed(self) -> None:
        self.generate()
        self.store.connection.execute("DELETE FROM blobs")
        with self.assertRaisesRegex(ValueError, "artifact"): portable_material(self.store)

    def test_records_detect_history_gap_duplicate_event_and_unreferenced_bytes(self) -> None:
        _, files = portable_material(self.store)
        for mutation in ("gap", "event", "extra"):
            changed = dict(files); records = document(changed["records.json"])
            if mutation == "gap": records["snapshots"][-1]["revision"] = 9
            if mutation == "event": records["events"][0]["sequence"] = 9
            if mutation == "extra": changed["blobs/" + "a" * 64 + ".txt"] = b"extra"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_records(records, changed)

    def test_portable_late_unsafe_name_is_refused_before_payload_read(self) -> None:
        source, plan = self.input_fixture("unsafe")
        plan["files"].append({"path": "../.env", "sha256": "a" * 64, "size_bytes": 1})
        (source / "openrtl-session.json").write_bytes(canonical(plan) + b"\n")
        from openrtl.adapters.design_export import _read
        with patch("openrtl.adapters.design_portable._read", wraps=_read) as reads:
            with self.assertRaisesRegex(ValueError, "path_invalid"): portable_input(source)
            self.assertEqual(reads.call_count, 1)  # Only the explicitly selected manifest.

    def test_tampered_duplicate_linked_and_special_backup_files_fail(self) -> None:
        for mode in ("tamper", "duplicate", "link", "fifo", "missing"):
            source, plan = self.input_fixture(mode); path = source / "records.json"
            if mode == "tamper": path.write_bytes(b"{}\n")
            elif mode == "duplicate":
                plan["files"].append(plan["files"][0]); (source / "openrtl-session.json").write_bytes(canonical(plan) + b"\n")
            else:
                original = self.root / (mode + ".json"); path.rename(original)
                if mode == "link": path.symlink_to(original)
                if mode == "fifo": os.mkfifo(path)
            with self.subTest(mode=mode), self.assertRaises((ValueError, OSError)): portable_input(source)

    def test_interrupted_export_or_restore_is_marked_and_not_resumed_implicitly(self) -> None:
        source, plan = self.backup()
        destination = self.root / "partial"
        original = os.fsync; calls = 0
        def interrupted(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("synthetic failure")
            original(fd)
        with patch("openrtl.adapters.design_export.os.fsync", side_effect=interrupted):
            with self.assertRaises(OSError): restore_portable(source, destination, content_digest(plan))
        self.assertTrue((destination / "INCOMPLETE").is_file())
        with self.assertRaisesRegex(ValueError, "incomplete"): DesignSessionStore(destination, read_only=True)
        with self.assertRaises(ValueError): restore_portable(source, destination, content_digest(plan))

    def test_unknown_usage_is_not_zero_and_known_zero_remains_known(self) -> None:
        class UnknownExpert(FakeExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                return ExpertReply(reply.output, reply.provider, reply.model)
        self.agent.expert = UnknownExpert()
        self.generate()
        usage = inspect_session(self.store)["usage"]
        self.assertIsNone(usage["input_tokens"]["known_total"])
        self.assertEqual(usage["input_tokens"]["unknown_calls"], 1)
        state = self.store.read()
        self.store.save(state, state, "operation.received", {"operation_id": "d" * 32, "input_tokens": 0, "output_tokens": 7})
        usage = inspect_session(self.store)["usage"]
        self.assertEqual(usage["input_tokens"]["known_total"], 0)
        self.assertEqual(usage["output_tokens"]["known_total"], 7)

    def test_reconstructed_database_is_known_schema_with_exact_history_and_blobs(self) -> None:
        self.generate(); plan, files = portable_material(self.store)
        states = validate_records(document(files["records.json"]), files)
        restored, output = restored_material(plan, files, states)
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(output["session.sqlite3"])
            self.assertEqual({r[0] for r in connection.execute("SELECT name FROM sqlite_master") if not r[0].startswith("sqlite_")}, {"snapshots", "events", "blobs"})
            actual = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM snapshots ORDER BY revision")]
            self.assertEqual(actual, [*states, restored])
            self.assertEqual({r[0]: r[1] for r in connection.execute("SELECT digest, content FROM blobs")},
                             {"sha256:" + p[6:-4]: b for p, b in files.items() if p.startswith("blobs/")})
            self.assertEqual(restored["calls"], self.store.read()["calls"])
            self.assertIsNone(restored["active"])
        finally: connection.close()

    def test_portable_import_original_and_changed_current_blob_both_retained(self) -> None:
        from openrtl.adapters.design_imports import import_design_files
        from openrtl.adapters.design_selection import selection
        source = self.root / "original"; source.mkdir(); (source / "notes.md").write_text("Original notes\n")
        plan, _, _ = selection(source, ["notes.md"])
        import_design_files(self.store, source, plan, content_digest(plan))
        self.agent.approve(content_digest(self.store.read()["spec"]))
        state = self.store.read()
        self.store.save(state, state, "operation.completed", files=[{"path": "docs/notes.md", "content": "Revised notes\n"}])
        _, files = portable_material(self.store)
        self.assertIn(b"Original notes\n", files.values())
        self.assertIn(b"Revised notes\n", files.values())

    def test_portable_reverifies_bound_historical_run_without_execution(self) -> None:
        from tests.test_design_coaching import DesignCoachingTest
        from openrtl.domain.design_session import STAGES
        from tests.test_design_agent import FakeSimulator
        self.agent.simulator = FakeSimulator()
        self.agent.approve(content_digest(self.store.read()["spec"]))
        for _ in STAGES: asyncio.run(self.agent.advance())
        asyncio.run(self.agent.advance())
        # Replace the synthetic legacy receipt in this fixture's latest snapshot.
        # This exercises byte binding; it is never evidence of an actual simulation.
        fixture = DesignCoachingTest(); fixture.root, fixture.store = self.store.root, self.store
        state = fixture.recorded_run()
        # Preserve the historical revision number in that fixture-only replacement.
        previous = copy.deepcopy(state); previous["revision"] -= 1
        self.store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=?", (canonical(previous).decode(), previous["revision"]))
        plan, files = portable_material(self.store)
        states = validate_records(document(files["records.json"]), files)
        _, restored = restored_material(plan, files, states)
        relative = "runs/" + state["simulation"]["run_id"] + "/evidence/waves.vcd"
        self.assertEqual(restored[relative], (self.store.root / relative).read_bytes())
        (self.store.root / relative).write_bytes(b"changed")
        with self.assertRaises(ValueError): portable_material(self.store)

    def test_support_excludes_narrative_provider_names_errors_paths_and_sources(self) -> None:
        self.generate(); state = self.store.read()
        self.store.save(state, state, "operation.received", {"provider": "SYNTHETIC_PRIVATE", "model": "SYNTHETIC_PRIVATE",
                                                            "error_code": "SYNTHETIC_PRIVATE"})
        report = inspect_session(self.store, limit=2); encoded = json.dumps(report)
        self.assertNotIn("SYNTHETIC_PRIVATE", encoded)
        self.assertNotIn(state["spec"]["behavior"], encoded)
        self.assertNotIn(str(self.store.root), encoded)
        self.assertEqual(len(report["events"]), 2)
        self.assertGreater(report["omitted_events"], 0)
        self.assertEqual(diagnostic(OSError("SYNTHETIC_PRIVATE"))["code"], "local_operation_failed")
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(diagnostic(ValueError("SYNTHETIC_PRIVATE"))))

    def test_support_preview_writes_nothing_and_export_matches_approved_bytes(self) -> None:
        target = self.root / "support"
        plan = support_bundle(self.store, target)
        self.assertFalse(target.exists())
        support_bundle(self.store, target, content_digest(plan))
        self.assertEqual(json.loads((target / "diagnostics.json").read_bytes()), plan)
        self.assertEqual({p.name for p in target.iterdir()}, {"diagnostics.json", "openrtl-support.json"})

    def test_cli_preview_restore_and_state_directory_forwarding(self) -> None:
        from openrtl.onboarding import _forwarded_arguments
        for args in (["--state-dir", "/synthetic", "sessions", "list"], ["--state-dir=/synthetic", "sessions", "list"]):
            forwarded = _forwarded_arguments(args)
            assert forwarded is not None
            self.assertEqual(forwarded[0], "sessions")
            self.assertIn("list", forwarded)
            self.assertTrue(any("state-dir" in a for a in forwarded))
        output = io.StringIO(); target = self.root / "cli backup"
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["sessions", "export", "--project", str(self.store.root), "--destination", str(target)]), 0)
        self.assertFalse(target.exists())
        self.assertIn("Includes source", output.getvalue())

    def test_event_correlations_bind_updated_artifacts_and_operation(self) -> None:
        self.generate()
        row = self.store.events()[-1]
        self.assertEqual(row["fields"]["artifacts_digest"], content_digest(self.store.read()["files"]))
        self.assertEqual(row["fields"]["spec_digest"], content_digest(self.store.read()["spec"]))
        self.assertEqual(len(row["fields"]["operation_id"]), 32)

    def test_portable_snapshot_refuses_concurrent_revision_after_read_transaction(self) -> None:
        original = self.store.read
        calls = 0
        def changed() -> JsonObject:
            nonlocal calls
            calls += 1
            state = original()
            if not self.store.connection.in_transaction:
                state = copy.deepcopy(state); state["detail"] = "brief"
            return state
        with patch.object(self.store, "read", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "session_changed"): portable_material(self.store)
        self.assertGreater(calls, 0)

    def test_readonly_cli_list_and_inspect_create_no_state(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["sessions", "--state-dir", str(self.root), "list"]), 0)
            self.assertEqual(main(["sessions", "inspect", "--project", str(self.store.root)]), 0)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"projects"})

    def test_legacy_history_survives_explicit_upgrade_and_portable_reconstruction(self) -> None:
        from openrtl.domain.design_session import legacy_initial_state
        legacy = legacy_initial_state()
        other = DesignSessionStore(self.root / "legacy", create=True)
        try:
            other.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0", (canonical(legacy).decode(),))
            with self.assertRaisesRegex(ValueError, "upgrade"): portable_material(other)
            other.upgrade(); plan, files = portable_material(other)
            states = validate_records(document(files["records.json"]), files)
            restored, _ = restored_material(plan, files, states)
            self.assertEqual(states[0], legacy)
            self.assertEqual(restored["calls"], legacy["calls"])
        finally: other.close()

    def test_v4_upgrade_keeps_history_and_adds_empty_web_state(self) -> None:
        from openrtl.domain.design_session import coaching_initial_state
        prior = coaching_initial_state()
        other = DesignSessionStore(self.root / "coaching-v4", create=True)
        try:
            other.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0",
                                     (canonical(prior).decode(),))
            upgraded = other.upgrade()
            self.assertEqual(upgraded["engineering_memory"], [])
            self.assertEqual(upgraded["workspace_operations"], {})
            plan, files = portable_material(other)
            states = validate_records(document(files["records.json"]), files)
            self.assertEqual(states[0], prior)
            self.assertEqual(states[-1], upgraded)
            self.assertEqual(restored_material(plan, files, states)[0]["engineering_memory"], [])
        finally:
            other.close()

    def test_observer_emits_waiting_and_failure_cannot_repeat_work(self) -> None:
        async def scenario() -> None:
            release = asyncio.Event(); rows = []; count = 0
            async def work() -> int:
                nonlocal count
                count += 1
                await release.wait()
                return 3
            def emit(row: JsonObject) -> None:
                rows.append(row)
                if row["phase"] == "waiting": release.set()
                raise RuntimeError("observer failure")
            state = self.store.read(); state["active"] = {"id": "a" * 32, "stage": "discovery", "kind": "expert"}
            self.assertEqual(await observed(work(), state, emit, interval=0.001), 3)
            self.assertEqual(count, 1)
            self.assertEqual([r["phase"] for r in rows], ["started", "waiting", "returned_for_validation"])
            self.assertNotIn("spec", rows[0])
        asyncio.run(scenario())

    def test_progress_cancellation_preserves_charged_intent(self) -> None:
        class SlowExpert:
            async def generate(self, stage: str, context: JsonObject, operation: str) -> ExpertReply:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
        async def scenario() -> None:
            agent = DesignAgent(self.store, SlowExpert())
            task = asyncio.create_task(agent.discuss("synthetic private request"))
            await asyncio.sleep(0.01); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(self.store.read()["calls"], 1)
            self.assertIsNotNone(self.store.read()["active"])
            with self.assertRaisesRegex(ValueError, "reconciliation"): await agent.discuss("retry")
        asyncio.run(scenario())
