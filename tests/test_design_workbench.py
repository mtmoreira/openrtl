"""Provider-free workbench views over real saved session revisions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_workbench import DesignWorkbench
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import STAGES, content_digest
from tests.test_design_agent import FakeExpert, FakeSimulator, manifest, specification


class DesignWorkbenchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = DesignSessionStore(Path(self.temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.agent = DesignAgent(self.store, self.expert)
        self.workbench = DesignWorkbench(self.store)
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        for _ in STAGES:
            asyncio.run(self.agent.advance())

    def test_source_identity_history_and_planned_links(self) -> None:
        current = self.store.read()["revision"]
        inventory = self.workbench.inventory(current)
        self.assertEqual(inventory["planned"]["top"], "wire_top")
        self.assertEqual(inventory["requirements"][0]["planned_tests"], ["transfer"])
        self.assertEqual(inventory["requirements"][0]["link_status"], "planned_not_coverage")
        self.assertEqual(inventory["elaborated"]["status"], "unavailable")
        file = next(row for row in inventory["files"] if row["path"] == "rtl/wire_top.sv")
        source = self.workbench.source(file["revision"], file["path"], file["digest"])
        self.assertIn("module wire_top", source["content"])
        self.assertEqual(self.workbench.diff(current - 1, current, file["path"])["after_digest"], file["digest"])
        with self.assertRaisesRegex(ValueError, "identity_stale"):
            self.workbench.source(current, file["path"], "sha256:" + "0" * 64)
        with self.assertRaisesRegex(ValueError, "source_path_invalid"):
            self.workbench.source(current, "../.env", file["digest"])

    def test_question_uses_validated_attachment_and_does_not_save_prompt(self) -> None:
        async def scenario() -> None:
            workspace = DesignWorkspace(self.agent)
            current = self.store.read()["revision"]
            file = next(row for row in self.workbench.inventory(current)["files"]
                        if row["path"] == "rtl/wire_top.sv")
            attachment = {"revision": current, "path": file["path"], "digest": file["digest"],
                          "start_line": 1, "end_line": 1}
            identifier = uuid.uuid4().hex
            await workspace.submit_question("PRIVATE QUESTION", client_operation_id=identifier,
                                            expected_revision=current, attachment=attachment)
            await workspace._tasks[identifier]
            self.assertEqual(workspace.operation(identifier)["phase"], "completed")
            self.assertIn("revision " + str(current), str(self.expert.seen[-1]))
            self.assertNotIn("PRIVATE QUESTION", str(self.store.read()))
            self.assertNotIn("PRIVATE QUESTION", str(self.store.events()))
            with self.assertRaisesRegex(ValueError, "identity_stale"):
                await workspace.submit_question("invalid", client_operation_id=uuid.uuid4().hex,
                                                expected_revision=self.store.read()["revision"],
                                                attachment={**attachment, "digest": "bad"})
        asyncio.run(scenario())

    def test_change_request_saves_reviewable_proposal_without_applying_it(self) -> None:
        async def scenario() -> None:
            paths = {stage: [] for stage in STAGES}
            paths["dv"] = ["dv/test_wire.py"]
            self.expert.responses["change_planning"] = {
                "summary": "Fixture DV change", "specification": specification(),
                "stage_paths": paths, "manifest": manifest()}
            workspace = DesignWorkspace(self.agent)
            before = self.store.read()
            identifier = uuid.uuid4().hex
            await workspace.submit_question("PRIVATE CHANGE", client_operation_id=identifier,
                                            expected_revision=before["revision"], kind="change", intent="dv")
            await workspace._tasks[identifier]
            after = self.store.read()
            self.assertEqual(workspace.operation(identifier)["phase"], "completed")
            self.assertIsNotNone(after["proposal"])
            self.assertEqual(after["files"], before["files"])
            self.assertEqual(workspace.review("change")["payload"], after["proposal"])
            self.assertNotIn("PRIVATE CHANGE", str(after))
        asyncio.run(scenario())

    def test_compiler_index_requires_exact_saved_source(self) -> None:
        current = self.store.read()["revision"]
        tree = {"type": "NETLIST", "modulesp": [{"type": "MODULE", "name": "wire_top",
                "origName": "wire_top", "addr": "(T)", "loc": "e,1:1,1:10", "stmtsp": []}]}
        meta = {"files": {"e": {"filename": "/tmp/owned/rtl/wire_top.sv"}}}
        index = self.workbench.index_compiler_output(
            json.dumps(tree).encode(), json.dumps(meta).encode(), revision=current,
            source_root="/tmp/owned", tool_version="5.046", options=["--top-module", "wire_top"])
        self.assertEqual(self.workbench.inventory(current)["elaborated"], index)
        changed = {**index, "instances": [{**index["instances"][0],
                   "definition": {**index["instances"][0]["definition"], "digest": "bad"}}]}
        with self.assertRaisesRegex(ValueError, "source_stale"):
            self.workbench.use_elaborated_index(changed)

    def test_simulation_submission_is_reviewed_and_idempotent(self) -> None:
        async def scenario() -> None:
            simulator = FakeSimulator()
            self.agent.simulator = simulator
            workspace = DesignWorkspace(self.agent)
            plan = workspace.simulation_plan()
            self.assertEqual(plan["top"], "wire_top")
            self.assertEqual(plan["expected_tests"], ["transfer"])
            with self.assertRaisesRegex(ValueError, "plan_stale"):
                await workspace.submit_simulation(client_operation_id=uuid.uuid4().hex,
                                                  expected_revision=plan["revision"], plan_digest="bad")
            identifier = uuid.uuid4().hex
            queued = await workspace.submit_simulation(client_operation_id=identifier,
                                                       expected_revision=plan["revision"],
                                                       plan_digest=plan["plan_digest"])
            self.assertEqual(queued["phase"], "queued")
            await workspace._tasks[identifier]
            self.assertEqual(workspace.operation(identifier)["phase"], "completed")
            self.assertEqual(len(simulator.calls), 1)
            self.assertEqual(self.store.read()["simulation"]["status"], "passed")
            retry = await workspace.submit_simulation(client_operation_id=identifier,
                                                      expected_revision=plan["revision"],
                                                      plan_digest=plan["plan_digest"])
            self.assertEqual(retry["phase"], "completed")
            self.assertEqual(len(simulator.calls), 1)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
