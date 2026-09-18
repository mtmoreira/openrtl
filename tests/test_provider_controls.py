"""Provider configuration and conservative accounting with synthetic identities only."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.provider_invocation import read_api_key_stdin
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply
from openrtl.domain.design_session import JsonObject, WEB_SESSION_SCHEMA, canonical, web_initial_state
from openrtl.domain.provider_controls import (
    compatible_model, estimated_cost_nano, model_catalog, request_reserve_nano, spend_limit_nano,
)
from tests.test_design_agent import FakeExpert


class MeteredExpert(FakeExpert):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        reply = await super().generate(stage, context, operation_id)
        return ExpertReply(reply.output, "openai", "gpt-5.6-terra", 100, 50)


class ProviderControlsTest(unittest.TestCase):
    def test_reviewed_catalog_rejects_unknown_aliases_and_prices_reserve(self) -> None:
        self.assertEqual(len(model_catalog()), 5)
        self.assertEqual(compatible_model("gpt-5.6-terra"), "gpt-5.6-terra")
        self.assertEqual(compatible_model("gpt-5.4-nano-2026-03-17"), "gpt-5.4-nano-2026-03-17")
        self.assertEqual(estimated_cost_nano("gpt-5.4-nano-2026-03-17", 300_000, 1_000),
                         61_250_000)
        for model in ("gpt-5.6", "gpt-4o", "scripted-expert-v1", ""):
            with self.assertRaisesRegex(ValueError, "provider_model_incompatible"):
                compatible_model(model)
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
