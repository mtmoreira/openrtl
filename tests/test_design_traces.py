"""Synthetic private inspection records; never provider calls or real prompts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from openrtl.adapters.design_portable import portable_material
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore, MAX_CONTENT_BYTES
from openrtl.application.design_agent import DesignAgent, ExpertReply
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import content_digest
from tests.test_design_agent import FakeExpert


class DesignTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = DesignSessionStore(Path(self.temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)

    def test_default_is_absent_and_capture_survives_readonly_reopen(self) -> None:
        identifier = uuid.uuid4().hex
        traces = DesignTraceStore(self.store)
        traces.record(identifier, "provider_request", {"prompt": "synthetic private example"})
        self.assertFalse(traces.status()["available"])
        self.assertEqual(traces.records({identifier}), [])
        revision = self.store.read()["revision"]
        traces.set_enabled(True)
        traces.record(identifier, "provider_request", {"prompt": "synthetic private example"})
        self.assertEqual(self.store.read()["revision"], revision)
        readonly = DesignSessionStore(self.store.root, read_only=True)
        try:
            reopened = DesignTraceStore(readonly)
            self.assertFalse(reopened.enabled)
            self.assertEqual(reopened.records({identifier})[0]["payload"]["prompt"], "synthetic private example")
            with self.assertRaisesRegex(ValueError, "session_read_only"):
                reopened.set_enabled(True)
        finally:
            readonly.close()
        traces.set_enabled(False)
        traces.record(identifier, "provider_request", {"prompt": "not stored"})
        self.assertEqual(len(traces.records({identifier})), 1)

    def test_credentials_and_limits_are_explicit_and_never_in_portable_export(self) -> None:
        identifier = uuid.uuid4().hex
        traces = DesignTraceStore(self.store, enabled=True)
        token = "sk-" + "synthetic" * 8
        github_token = "ghp_" + "synthetic" * 4
        aws_key = "AKIA" + "EXAMPLE0" * 2
        traces.record(identifier, "provider_request", {
            "api_key": "synthetic-secret", "nested": {"Authorization": "Bearer synthetic-secret"},
            "content": 'User wrote api_key="synthetic-secret" and ' + token + " " + github_token + " " + aws_key,
            "input_tokens": 5,
        })
        row = traces.records({identifier})[0]
        self.assertNotIn("synthetic-secret", json.dumps(row))
        self.assertNotIn(token, json.dumps(row))
        self.assertNotIn(github_token, json.dumps(row))
        self.assertNotIn(aws_key, json.dumps(row))
        self.assertEqual(row["payload"]["input_tokens"], 5)
        traces.record(identifier, "provider_response", {"text": "x" * (MAX_CONTENT_BYTES + 1)})
        self.assertTrue(traces.records({identifier})[-1]["truncated"])
        with patch("openrtl.adapters.design_trace_store.MAX_RECORDS", 2):
            traces.record(identifier, "provider_response", {"text": "record omitted"})
        self.assertEqual(traces.status()["omitted_records"], 1)
        plan, files = portable_material(self.store)
        self.assertFalse(traces.status()["exported"])
        self.assertNotIn(b"provider_request", b"".join(files.values()))
        self.assertNotIn("private", str(plan))

    def test_tampered_rows_and_excessive_read_size_fail_closed(self) -> None:
        identifier = uuid.uuid4().hex
        traces = DesignTraceStore(self.store, enabled=True)
        traces.record(identifier, "user_input", {"text": "synthetic"})
        self.store.connection.execute("UPDATE private_traces SET truncated=2")
        with self.assertRaisesRegex(ValueError, "private_trace_record_invalid"):
            traces.records({identifier})
        self.store.connection.execute("UPDATE private_traces SET truncated=0")
        with patch("openrtl.adapters.design_trace_store.MAX_TOTAL_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "private_trace_read_limit"):
                traces.records({identifier})
        self.store.connection.execute("UPDATE private_traces SET content_bytes=1")
        with self.assertRaisesRegex(ValueError, "private_trace_record_invalid"):
            traces.records({identifier})

    def test_conversation_correlation_and_reload_are_durable(self) -> None:
        traces = DesignTraceStore(self.store, enabled=True)
        expert = FakeExpert()
        expert.responses["discovery"] = {"reply": "Choose a counter width.", "specification": None,
            "engineering_memory": [{"id": "counter.width", "kind": "question",
                "text": "Counter width is unresolved", "provenance": "agent_proposal"}],
            "questions_asked": ["counter.width"]}
        agent = DesignAgent(self.store, expert, trace_store=traces)
        workspace = DesignWorkspace(agent)
        identifier = uuid.uuid4().hex

        async def run() -> None:
            await workspace.submit_discussion("Synthetic counter prompt", client_operation_id=identifier,
                                              expected_revision=0)
            await workspace._tasks[identifier]
        asyncio.run(run())
        final = self.store.events()[-1]
        detail = workspace.history(final["sequence"])
        self.assertIn("operation.received", {row["event"] for row in detail["trace"]})
        self.assertIn("workspace.requested", {row["event"] for row in detail["trace"]})
        self.assertEqual(detail["metrics"]["input_tokens"], 10)
        self.assertEqual(detail["metrics"]["output_tokens"], 20)
        self.assertIsNone(detail["metrics"]["estimated_cost_nano_usd"])
        self.assertIsNone(detail["metrics"]["reasoning_tokens"])
        self.assertEqual(detail["visibility"]["raw_prompt"], "captured")
        reopened = DesignWorkspace(DesignAgent(self.store, trace_store=DesignTraceStore(self.store)))
        self.assertEqual(reopened.operation(identifier)["reply"],
                         "Please clarify these design decisions:\n\n1. Counter width is unresolved")
        self.assertEqual([row["text"] for row in reopened.conversation()["messages"]],
                         ["Synthetic counter prompt",
                          "Please clarify these design decisions:\n\n1. Counter width is unresolved"])
        self.assertNotIn("Synthetic counter prompt", str(workspace.snapshot()))

    def test_failed_call_remains_inspectable_with_unknown_usage(self) -> None:
        expert = FakeExpert()
        expert.fail = True
        workspace = DesignWorkspace(DesignAgent(self.store, expert,
            trace_store=DesignTraceStore(self.store, enabled=True)))
        identifier = uuid.uuid4().hex

        async def run() -> None:
            await workspace.submit_discussion("Synthetic failure prompt", client_operation_id=identifier,
                                              expected_revision=0)
            await workspace._tasks[identifier]
        asyncio.run(run())
        operation = workspace.operation(identifier)
        self.assertEqual(operation["phase"], "failed")
        self.assertIsInstance(operation["result_revision"], int)
        detail = workspace.history(self.store.read()["revision"])
        self.assertIn("operation.failed", {row["event"] for row in detail["trace"]})
        self.assertIsNone(detail["metrics"]["input_tokens"])
        self.assertIsInstance(detail["metrics"]["elapsed_ms"], int)
        self.assertIn("provider_failure", {row["category"] for row in detail["details"]})

    def test_failed_provider_metrics_and_returned_reasoning_are_visible(self) -> None:
        traces = DesignTraceStore(self.store, enabled=True)

        class InvalidExpert:
            async def generate(self, stage: str, context: dict[str, object], operation_id: str) -> ExpertReply:
                traces.record(operation_id, "provider_metrics", {"input_tokens": 42, "output_tokens": 9,
                    "provider_metadata": {"provider": "ollama", "model": "synthetic:1b",
                        "finish_reason": "length", "load_duration_ns": "3000000"}})
                traces.record(operation_id, "provider_trace", {"kind": "provider.response", "truncated": False,
                    "content": json.dumps({"thinking": "Synthetic public provider reasoning", "output": "invalid"})})
                raise ValueError("expert_result_identity_or_finish_invalid")

        agent = DesignAgent(self.store, InvalidExpert(), trace_store=traces)
        with self.assertRaises(ValueError):
            asyncio.run(agent.discuss("Synthetic failure telemetry"))
        detail = DesignWorkspace(agent).history(self.store.read()["revision"])
        self.assertEqual(detail["metrics"]["input_tokens"], 42)
        self.assertEqual(detail["metrics"]["output_tokens"], 9)
        self.assertEqual(detail["metrics"]["load_duration_ns"], 3000000)
        self.assertEqual(detail["metrics"]["calls"][0]["model"], "synthetic:1b")
        self.assertEqual(detail["visibility"]["provider_reasoning"], "captured")
        self.assertIsNone(detail["metrics"]["reasoning_tokens"])
        self.assertIsNone(detail["metrics"]["estimated_cost_nano_usd"])

    def test_changed_files_are_revision_bound_and_remain_untrusted(self) -> None:
        agent = DesignAgent(self.store, FakeExpert())
        asyncio.run(agent.discuss("Synthetic reviewed wire"))
        agent.approve(content_digest(self.store.read()["spec"]))
        asyncio.run(agent.advance())
        workspace = DesignWorkspace(agent)
        detail = workspace.history(self.store.read()["revision"])
        self.assertEqual(len(detail["files"]), 1)
        row = detail["files"][0]
        self.assertEqual(row["change"], "created")
        self.assertTrue(row["untrusted"])
        source = workspace.workbench.source(row["revision"], row["path"], row["digest"])
        self.assertIn("Requirement wire.transfer", source["content"])

    def test_failed_openai_style_lifecycle_usage_is_merged_once(self) -> None:
        traces = DesignTraceStore(self.store, enabled=True)

        class FailedDecodeExpert:
            async def generate(self, stage: str, context: dict[str, object], operation_id: str) -> ExpertReply:
                attributes = {"provider": "openai", "model": "synthetic-model", "input_tokens": 80,
                              "output_tokens": 20, "cached_input_tokens": 10, "reasoning_tokens": 3,
                              "failure_code": "openai.responses.invalid_output", "elapsed_ms": 7}
                for kind in ("usage.reported", "provider_call.completed"):
                    traces.record(operation_id, "runtime_event", {"kind": kind, "attributes": attributes})
                raise ValueError("expert_reply_invalid")

        agent = DesignAgent(self.store, FailedDecodeExpert(), trace_store=traces)
        with self.assertRaises(ValueError):
            asyncio.run(agent.discuss("Synthetic decode failure"))
        detail = DesignWorkspace(agent).history(self.store.read()["revision"])
        self.assertEqual(detail["metrics"]["input_tokens"], 80)
        self.assertEqual(detail["metrics"]["output_tokens"], 20)
        self.assertEqual(detail["metrics"]["cached_input_tokens"], 10)
        self.assertEqual(detail["metrics"]["reasoning_tokens"], 3)
        self.assertEqual(detail["metrics"]["calls"][0]["provider"], "openai")
        self.assertIsNone(detail["metrics"]["estimated_cost_nano_usd"])
        self.assertEqual(detail["visibility"]["provider_reasoning"], "not_recorded")

    def test_process_capture_is_observed_without_fabricating_model_usage(self) -> None:
        traces = DesignTraceStore(self.store, enabled=True)
        identifier = uuid.uuid4().hex
        state = self.store.read()
        started = self.store.save(state, {**state, "active": {
            "id": identifier, "kind": "simulation", "stage": "simulation"}}, "operation.started",
            {"operation_id": identifier, "role": "simulation"})
        self.store.save(started, {**started, "active": None}, "operation.completed", {"elapsed_ms": 25})
        traces.record(identifier, "provider_trace", {"kind": "process.request", "truncated": False,
            "content": json.dumps({"argv": ["synthetic-compiler", "--check"]})})
        traces.record(identifier, "provider_trace", {"kind": "process.response", "truncated": False,
            "content": json.dumps({"stdout": "Synthetic output", "stderr": "", "exit_code": 0})})
        # An unrelated usage-shaped field on a process invocation is not LLM usage.
        traces.record(identifier, "runtime_event", {"kind": "usage.reported",
                                                    "attributes": {"input_tokens": 999}})
        detail = DesignWorkspace(DesignAgent(self.store, trace_store=traces)).history(2)
        self.assertEqual(detail["metrics"]["calls"], [])
        self.assertIsNone(detail["metrics"]["input_tokens"])
        self.assertEqual(detail["visibility"]["observed_process_requests"], 1)
        self.assertEqual(detail["visibility"]["observed_process_responses"], 1)
        self.assertEqual(detail["visibility"]["shell_output"], "process_output_captured")
        self.assertEqual(detail["visibility"]["tool_calls"], "not_recorded")
        self.assertEqual(detail["visibility"]["provider_reasoning"], "not_recorded")

    def test_expert_progress_is_reported_without_narrative(self) -> None:
        workspace = DesignWorkspace(DesignAgent(self.store))
        identifier = uuid.uuid4().hex
        workspace._on_progress({"schema": "openrtl.design-progress.v1", "stage": "discovery",
            "phase": "waiting", "elapsed_ms": 5000, "operation_id": identifier,
            "untrusted_text": "not retained"})
        self.assertEqual(workspace.snapshot()["progress"], {"stage": "discovery", "phase": "waiting",
            "elapsed_ms": 5000, "operation_id": identifier})

    def test_discussion_binds_and_clears_live_progress(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class WaitingExpert(FakeExpert):
            async def generate(self, stage: str, context: dict[str, object], operation_id: str) -> ExpertReply:
                entered.set()
                await release.wait()
                return await super().generate(stage, context, operation_id)

        expert = WaitingExpert()
        agent = DesignAgent(self.store, expert)
        workspace = DesignWorkspace(agent)

        async def run() -> None:
            identifier = uuid.uuid4().hex
            await workspace.submit_discussion("Synthetic progress request", client_operation_id=identifier,
                                              expected_revision=0)
            await entered.wait()
            self.assertEqual(workspace.snapshot()["progress"]["stage"], "discovery")
            release.set()
            await workspace._tasks[identifier]
            self.assertIsNone(workspace.snapshot()["progress"])
            self.assertIsNone(agent.progress)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
