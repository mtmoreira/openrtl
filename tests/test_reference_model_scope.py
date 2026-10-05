"""Reviewed reference-model authority through synthetic native Ollama responses."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from agentrig.integrations.ollama import OllamaAgentRuntime, OllamaRuntimeOptions
from agentrig.integrations.ollama.sdk import OllamaSdkClientFactory
from openrtl.adapters.design_generation import OllamaDesignExpert, _schema
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_local_review import approve_local, review_plan
from openrtl.domain.design_session import JsonObject, STAGES, content_digest
from tests.test_design_agent import FakeExpert, FakeSimulator, manifest
from tests.test_design_conversation import ready_spec
from tests.test_design_transport_runtime import MODEL, ScriptedRawSdk


def response() -> JsonObject:
    return {"summary": "Synthetic reviewed model revision", "manifest": None, "files": [
        {"relative_path": "blocks/__init__.py", "content": "# Revised synthetic package.\n"},
        {"relative_path": "blocks/wire.py", "content": "# Revised synthetic model.\n"},
        {"relative_path": "test_model.py", "content":
            "# Revised synthetic test; no generated code is executed.\n"},
    ]}


class ReferenceModelScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = DesignSessionStore(Path(temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        expert = FakeExpert()
        expert.responses["reference_model"] = {
            "summary": "Synthetic multi-file model baseline", "manifest": None,
            "files": [{"path": "model/" + row["relative_path"], "content": "# Original fixture.\n"}
                      for row in response()["files"]]}
        self.simulator = FakeSimulator()
        self.agent = DesignAgent(self.store, expert, self.simulator)
        self.agent.propose(ready_spec())
        self.agent.approve(content_digest(ready_spec()))
        for _ in STAGES:
            asyncio.run(self.agent.advance())
        baseline = self.store.read()
        self.assertEqual(baseline["stage"], len(STAGES))
        self.assertIsNone(baseline["simulation"])
        self.paths = ["model/" + row["relative_path"] for row in response()["files"]]
        plan = self.agent.plan_change({"specification": ready_spec(), "manifest": manifest(),
            "stage_paths": {stage: self.paths if stage == "reference_model" else []
                            for stage in STAGES}})
        displayed: list[str] = []
        review = review_plan(self.agent, "change", plan, displayed.append)
        self.assertTrue(any("Writable reference_model: " in row for row in displayed))
        approved = approve_local(self.agent, review, "change")
        self.assertEqual(approved["change_plan"], plan)
        for _ in range(2):
            asyncio.run(self.agent.advance())  # Unchanged documentation stages are retained.
        self.assertEqual(self.store.read()["stage"], 2)
        self.assertEqual(self.store.read()["calls"], baseline["calls"])
        self.trace = DesignTraceStore(self.store, enabled=True)
        self.agent.trace_store = self.trace

    def attach_responses(self, outputs: list[JsonObject]) -> ScriptedRawSdk:
        raw = ScriptedRawSdk(outputs)
        schema_id, schema = _schema("reference_model", {}, ollama=True)
        runtime = OllamaAgentRuntime(
            client_factory=OllamaSdkClientFactory(host="http://127.0.0.1:11434",
                                                 raw_client_builder=lambda _host, _headers: raw),
            model=MODEL, output_schemas={schema_id: schema},
            options=OllamaRuntimeOptions(temperature=0, max_output_tokens=16000, think=False))
        expert = OllamaDesignExpert(runtime, model=MODEL)
        expert.trace_store = self.trace
        self.agent.expert = expert
        self.agent.configure_provider("ollama/" + MODEL, None)
        return raw

    def test_decoded_exact_reviewed_multifile_paths_are_accepted(self) -> None:
        output = response()
        raw = self.attach_responses([output])
        before = self.store.read()
        old_contents = self.store.contents(before)
        after = asyncio.run(self.agent.advance())
        self.assertEqual((after["stage"], after["calls"]), (3, before["calls"] + 1))
        for field in ("spec", "approved_spec", "change_plan", "provider"):
            self.assertEqual(after[field], before[field], field)
        expected = {"model/" + row["relative_path"]: row["content"] for row in output["files"]}
        contents = self.store.contents(after)
        self.assertEqual({path: contents[path] for path in self.paths}, expected)
        for path, content in old_contents.items():
            if path not in self.paths:
                self.assertEqual(contents[path], content)
                self.assertEqual(after["files"][path], before["files"][path])
        self.assertEqual(set(contents), set(old_contents))
        self.assertIsNone(after["simulation"])
        self.assertEqual(self.simulator.calls, [])
        self.assertEqual(len(raw.calls), 1)
        receipt = next(event for event in reversed(self.store.events())
                       if event["event"] == "operation.received")
        self.assertEqual((receipt["fields"]["input_tokens"], receipt["fields"]["output_tokens"]),
                         (101, 203))
        capture = next(row["payload"]["output"] for row in
                       self.trace.records({receipt["fields"]["operation_id"]})
                       if row["category"] == "assistant_output")
        self.assertEqual(capture["files"], [{"path": path, "content": expected[path]}
                                            for path in self.paths])

    def test_decoded_extra_or_wrong_paths_do_not_expand_reviewed_authority(self) -> None:
        extra = response()
        extra["files"].append({"relative_path": "extra.py", "content": "# Unapproved fixture.\n"})
        wrong = response()
        wrong["files"][1]["relative_path"] = "blocks/unapproved.py"
        raw = self.attach_responses([extra, wrong])
        for index, output in enumerate((extra, wrong), start=1):
            with self.subTest(index=index):
                before = self.store.read()
                old_contents = self.store.contents(before)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(self.agent.advance())
                after = self.store.read()
                self.assertEqual(after["stage"], 2)
                self.assertEqual(after["calls"], before["calls"] + 1)
                self.assertIsNone(after["active"])
                self.assertEqual(after["last_error"], "expert_output_invalid")
                for field in ("spec", "approved_spec", "change_plan", "files", "summaries",
                              "manifest", "provider"):
                    self.assertEqual(after[field], before[field], field)
                self.assertEqual(self.store.contents(after), old_contents)
                failure = self.store.events()[-1]
                self.assertEqual(failure["event"], "operation.failed")
                self.assertEqual(failure["fields"]["validation_code"],
                                 "change_stage_paths_differ_from_review")
                receipt = self.store.events()[-2]
                self.assertEqual(receipt["event"], "operation.received")
                self.assertEqual((receipt["fields"]["input_tokens"], receipt["fields"]["output_tokens"]),
                                 (101, 203))
                captures = self.trace.records({receipt["fields"]["operation_id"]})
                captured = next(row["payload"]["output"] for row in captures
                                if row["category"] == "assistant_output")
                self.assertEqual(captured["files"], [
                    {"path": "model/" + row["relative_path"], "content": row["content"]}
                    for row in output["files"]])
                self.assertEqual(len(raw.calls), index)
                self.assertEqual(self.simulator.calls, [])


if __name__ == "__main__":
    unittest.main()
