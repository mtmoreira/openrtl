"""Provider configuration and conservative accounting with synthetic identities only."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from agentrig.core.errors import AgentRigError, Failure, FailureKind

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.provider_invocation import read_api_key_stdin
from openrtl.cli import parser
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply
from openrtl.application.provider_failures import classify_provider_failure
from openrtl.domain.design_session import JsonObject, WEB_SESSION_SCHEMA, canonical, web_initial_state
from openrtl.domain.provider_controls import (
    compatible_model, compatible_ollama_model, estimated_cost_nano, model_catalog,
    provider_selector, request_reserve_nano, selector_model, selector_provider, spend_limit_nano,
)
from tests.test_design_agent import FakeExpert


class MeteredExpert(FakeExpert):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        reply = await super().generate(stage, context, operation_id)
        return ExpertReply(reply.output, "openai", "gpt-5.6-terra", 100, 50)


class RejectedExpert(MeteredExpert):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        raise AgentRigError(Failure(
            kind=FailureKind.PERMANENT_PROVIDER,
            message="synthetic-private-provider-detail-do-not-persist",
            code="openai.responses.request_failed", metadata={"status_code": "401"}))


class LocalExpert(FakeExpert):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        reply = await super().generate(stage, context, operation_id)
        return ExpertReply(reply.output, "ollama", "qwen3:8b", 100, 50)


class RejectedLocalExpert(LocalExpert):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        raise AgentRigError(Failure(kind=FailureKind.PERMANENT_PROVIDER,
            message="synthetic-private-local-detail", code="ollama.request_failed",
            metadata={"status_code": "404"}))


class ProviderControlsTest(unittest.TestCase):
    def test_cli_accepts_explicit_local_provider_and_model(self) -> None:
        arguments = parser().parse_args(["ui", "--project", "/unit/project", "--allow-provider",
                                         "--provider", "ollama", "--model", "qwen3:8b"])
        self.assertEqual((arguments.provider, arguments.model), ("ollama", "qwen3:8b"))
        listing = parser().parse_args(["models", "--provider", "ollama"])
        self.assertEqual(listing.provider, "ollama")

    def test_normalized_failures_have_bounded_categories(self) -> None:
        for status, expected in (("400", "provider_request_rejected"),
                                 ("401", "provider_authentication_rejected"),
                                 ("429", "provider_rate_or_quota_limited"),
                                 ("526", "provider_service_unavailable")):
            error = AgentRigError(Failure(kind=FailureKind.PERMANENT_PROVIDER,
                message="synthetic-private-detail", code="openai.responses.request_failed",
                metadata={"status_code": status}))
            self.assertEqual(classify_provider_failure(error), expected)
        self.assertEqual(classify_provider_failure(RuntimeError("private-detail")),
                         "expert_invocation_failed")
        ollama = AgentRigError(Failure(kind=FailureKind.PERMANENT_PROVIDER,
            message="synthetic-private-detail", code="ollama.request_failed",
            metadata={"status_code": "404"}))
        self.assertEqual(classify_provider_failure(ollama), "provider_model_unavailable")

    def test_failed_call_keeps_reservation_until_explicit_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                agent = DesignAgent(store, RejectedExpert(),
                                    policy=DesignPolicy(provider_model="gpt-5.6-terra"))
                agent.configure_provider("gpt-5.6-terra", spend_limit_nano("5.00"))
                with self.assertRaisesRegex(ValueError, "provider_authentication_rejected"):
                    asyncio.run(agent.discuss("A wire"))
                failed = store.read()
                reserved = request_reserve_nano("gpt-5.6-terra", 16000)
                self.assertEqual(failed["last_error"], "provider_authentication_rejected")
                self.assertEqual(failed["provider"]["spent_nano_usd"], reserved)
                self.assertTrue(failed["provider"]["uncertain"])
                database_text = (store.root / "session.sqlite3").read_bytes().decode(errors="ignore")
                self.assertNotIn("synthetic-private-provider-detail", database_text)
                with self.assertRaisesRegex(ValueError, "provider_reconciliation_decision_invalid"):
                    agent.reconcile_provider_spend(failed["revision"], "refund")
                with self.assertRaisesRegex(ValueError, "workspace_revision_stale"):
                    agent.reconcile_provider_spend(failed["revision"] - 1, "retain_full_reservation")
                reconciled = agent.reconcile_provider_spend(failed["revision"], "retain_full_reservation")
                self.assertFalse(reconciled["provider"]["uncertain"])
                self.assertEqual(reconciled["provider"]["spent_nano_usd"], reserved)
                self.assertEqual(reconciled["calls"], 1)
                self.assertEqual(reconciled["last_error"], "provider_authentication_rejected")
                agent.configure_provider("gpt-5.6-terra", spend_limit_nano("10.00"))
                self.assertEqual(store.read()["provider"]["spent_nano_usd"], reserved)
            finally:
                store.close()

    def test_reviewed_catalog_rejects_unknown_aliases_and_prices_reserve(self) -> None:
        self.assertEqual(len(model_catalog()), 9)
        self.assertEqual(compatible_model("gpt-5.6-terra"), "gpt-5.6-terra")
        self.assertEqual(compatible_model("gpt-5.4-nano-2026-03-17"), "gpt-5.4-nano-2026-03-17")
        self.assertEqual(estimated_cost_nano("gpt-5.4-nano-2026-03-17", 300_000, 1_000),
                         61_250_000)
        self.assertEqual(compatible_model("gpt-4.1-mini"), "gpt-4.1-mini")
        self.assertEqual(compatible_model("gpt-4o"), "gpt-4o")
        for model in ("gpt-5.6", "gpt-3.5-turbo", "scripted-expert-v1", ""):
            with self.assertRaisesRegex(ValueError, "provider_model_incompatible"):
                compatible_model(model)
        with self.assertRaisesRegex(ValueError, "output_budget_invalid"):
            request_reserve_nano("gpt-4o-mini", 20_000)
        self.assertEqual(estimated_cost_nano("gpt-5.6-terra", 100, 50), 800_000)
        self.assertGreater(request_reserve_nano("gpt-5.6-terra", 16000),
                           estimated_cost_nano("gpt-5.6-terra", 100, 50))
        self.assertEqual(spend_limit_nano("20.00"), 20_000_000_000)
        for invalid in ("0.00", "1", "1.001", "1001.00", "-1.00", 20):
            with self.assertRaisesRegex(ValueError, "provider_spend_limit_invalid"):
                spend_limit_nano(invalid)

    def test_stdin_key_is_bounded_and_not_a_cli_argument(self) -> None:
        self.assertEqual(read_api_key_stdin(BytesIO(b"synthetic-key\n")), "synthetic-key")
        with self.assertRaisesRegex(ValueError, "provider_key_input_invalid"):
            read_api_key_stdin(BytesIO(b""))
        with self.assertRaisesRegex(ValueError, "provider_key_input_invalid"):
            read_api_key_stdin(BytesIO(b"x" * 4097))

    def test_project_ceiling_reserves_then_reconciles_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                expert = MeteredExpert()
                agent = DesignAgent(store, expert, policy=DesignPolicy(provider_model="gpt-5.6-terra"))
                agent.configure_provider("gpt-5.6-terra", spend_limit_nano("10.00"))
                asyncio.run(agent.discuss("A simple wire"))
                state = store.read()
                self.assertEqual(state["provider"]["spent_nano_usd"], 800_000)
                self.assertIsNone(state["provider"]["pending"])
                self.assertFalse(state["provider"]["uncertain"])
                self.assertEqual(state["calls"], 1)
                self.assertIn("estimated_cost_nano_usd", store.events()[-2]["fields"])
                agent.configure_provider("gpt-5.6-terra", spend_limit_nano("0.01"))
                with self.assertRaisesRegex(ValueError, "provider_spend_budget_exhausted"):
                    asyncio.run(agent.discuss("Another wire"))
                self.assertEqual(store.read()["calls"], 1)
            finally:
                store.close()

    def test_local_ollama_selection_has_no_usd_reservation(self) -> None:
        selector = provider_selector("ollama", "qwen3:8b")
        self.assertEqual(selector_provider(selector), "ollama")
        self.assertEqual(selector_model(selector), "qwen3:8b")
        self.assertEqual(compatible_ollama_model("library/qwen3:8b"), "library/qwen3:8b")
        for invalid in ("", "http://host/model", "model name", "../model"):
            with self.assertRaisesRegex(ValueError, "provider_model_incompatible"):
                compatible_ollama_model(invalid)
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                agent = DesignAgent(store, LocalExpert(), policy=DesignPolicy(provider_model=selector))
                agent.configure_provider(selector, None)
                asyncio.run(agent.discuss("A simple wire"))
                state = store.read()
                self.assertEqual(state["provider"]["model"], selector)
                self.assertIsNone(state["provider"]["limit_nano_usd"])
                self.assertEqual(state["provider"]["spent_nano_usd"], 0)
                self.assertFalse(state["provider"]["uncertain"])
                self.assertEqual(state["calls"], 1)
            finally:
                store.close()

    def test_local_failure_is_bounded_without_uncertain_cost(self) -> None:
        selector = provider_selector("ollama", "qwen3:8b")
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                agent = DesignAgent(store, RejectedLocalExpert(),
                                    policy=DesignPolicy(provider_model=selector))
                agent.configure_provider(selector, None)
                with self.assertRaisesRegex(ValueError, "provider_model_unavailable"):
                    asyncio.run(agent.discuss("A simple wire"))
                state = store.read()
                self.assertFalse(state["provider"]["uncertain"])
                self.assertEqual(state["provider"]["spent_nano_usd"], 0)
                self.assertEqual(state["last_error"], "provider_model_unavailable")
                self.assertNotIn("synthetic-private-local-detail",
                                 (store.root / "session.sqlite3").read_bytes().decode(errors="ignore"))
            finally:
                store.close()

    def test_uncertain_call_keeps_reservation_and_blocks_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                expert = MeteredExpert(); expert.fail = True
                agent = DesignAgent(store, expert, policy=DesignPolicy(provider_model="gpt-5.6-terra"))
                agent.configure_provider("gpt-5.6-terra", spend_limit_nano("10.00"))
                with self.assertRaisesRegex(ValueError, "expert_invocation_failed"):
                    asyncio.run(agent.discuss("A wire"))
                state = store.read()
                self.assertIsNone(state["active"])
                self.assertTrue(state["provider"]["uncertain"])
                self.assertEqual(state["provider"]["spent_nano_usd"],
                                 request_reserve_nano("gpt-5.6-terra", 16000))
                with self.assertRaisesRegex(ValueError, "provider_spend_uncertain"):
                    asyncio.run(agent.discuss("Retry"))
            finally:
                store.close()

    def test_v5_upgrade_marks_prior_calls_unpriced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                prior = web_initial_state()
                prior["calls"] = 2
                self.assertEqual(prior["schema"], WEB_SESSION_SCHEMA)
                store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0",
                                         (canonical(prior).decode(),))
                upgraded = store.upgrade()
                self.assertEqual(upgraded["provider"]["prior_unpriced_calls"], 2)
                self.assertEqual(upgraded["provider"]["spent_nano_usd"], 0)
                self.assertEqual(store.historical_state(0), prior)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
