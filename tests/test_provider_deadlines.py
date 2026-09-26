"""Provider-specific deadlines with synthetic runtimes and no network effects."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from openrtl.adapters.design_generation import AgentRigDesignExpert, OllamaDesignExpert
from openrtl.adapters.design_web import WorkspaceRuntime
from openrtl.application.design_agent import DesignPolicy
from openrtl.cli import parser
from openrtl.domain.provider_controls import provider_timeout_seconds
from tests.test_design_generation import ScriptedOllamaRuntime, generator


class ProviderDeadlinesTest(unittest.TestCase):
    def test_deadline_types_and_provider_specific_bounds(self) -> None:
        for provider, maximum in (("openai", 300), ("ollama", 900)):
            for value in (1, 120, maximum):
                with self.subTest(provider=provider, value=value):
                    self.assertEqual(provider_timeout_seconds(provider, value), value)
            for value in (0, -1, maximum + 1, True, False, 1.0, "120", None, []):
                with self.subTest(provider=provider, value=value):
                    with self.assertRaisesRegex(ValueError, "expert_timeout_invalid"):
                        provider_timeout_seconds(provider, value)
        for provider in ("remote", None, []):
            with self.assertRaisesRegex(ValueError, "provider_kind_invalid"):
                provider_timeout_seconds(provider, 120)

    def test_native_ollama_receives_full_explicit_deadline(self) -> None:
        runtime = ScriptedOllamaRuntime()
        adapter = OllamaDesignExpert(runtime, model="qwen3:8b", timeout_seconds=900)
        with patch("openrtl.adapters.design_generation.time.monotonic", return_value=100.0):
            asyncio.run(adapter.generate("reference_model", {}, "a" * 32))
        self.assertEqual(runtime.contexts[0].deadline.monotonic_deadline, 1000.0)
        self.assertEqual(len(runtime.requests), 1)

    def test_adapters_keep_defaults_and_reject_excess_before_dispatch(self) -> None:
        for adapter_type, runtime, model, maximum in (
            (AgentRigDesignExpert, generator(), "test-model", 300),
            (OllamaDesignExpert, ScriptedOllamaRuntime(), "qwen3:8b", 900),
        ):
            self.assertEqual(adapter_type(runtime, model=model).timeout_seconds, 120)
            self.assertEqual(adapter_type(runtime, model=model, timeout_seconds=maximum).timeout_seconds, maximum)
            for value in (maximum + 1, True, "120"):
                with self.subTest(adapter=adapter_type.__name__, value=value):
                    with self.assertRaisesRegex(ValueError, "expert_timeout_invalid"):
                        adapter_type(runtime, model=model, timeout_seconds=value)
        self.assertEqual(runtime.requests, [])

    def test_cli_accepts_explicit_local_long_deadline(self) -> None:
        options = parser().parse_args(["ui", "--project", "/tmp/synthetic-project",
            "--provider", "ollama", "--model", "qwen3:8b", "--timeout-seconds", "900"])
        self.assertEqual((options.provider, options.timeout_seconds), ("ollama", 900))

    def test_web_rejects_invalid_initial_deadlines_before_opening_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "project"
            factory = Mock()
            for provider, timeout in (("openai", 301), ("ollama", 901), ("ollama", True)):
                with self.assertRaisesRegex(ValueError, "expert_timeout_invalid"):
                    WorkspaceRuntime(root, create=True, expert_factory=factory,
                        policy=DesignPolicy(), initial_provider=provider, timeout_seconds=timeout)
                self.assertFalse(root.exists())
            factory.assert_not_called()

    def test_web_switch_rejects_incompatible_deadline_without_state_or_factory_effects(self) -> None:
        built = []

        def build(provider, model, key, timeout):
            built.append((provider, model, timeout))
            return OllamaDesignExpert(ScriptedOllamaRuntime(), model=model, timeout_seconds=timeout)

        with tempfile.TemporaryDirectory() as temporary:
            runtime = WorkspaceRuntime(Path(temporary).resolve() / "project", create=True,
                expert_factory=lambda: None, provider_builder=build, policy=DesignPolicy(),
                initial_provider="ollama", initial_model="qwen3:8b", timeout_seconds=900,
                detailed_capture=True)
            try:
                baseline = runtime.provider_settings()
                snapshot = runtime.call(lambda workspace: workspace.snapshot())
                self.assertEqual(baseline["timeout_seconds"], 900)
                self.assertEqual(baseline["timeout_limits_seconds"], {"openai": 300, "ollama": 900})
                self.assertEqual(built, [("ollama", "qwen3:8b", 900)])
                # Omitting the deadline preserves 900, but cannot carry it into OpenAI.
                for timeout in (None, 301, 900):
                    with self.assertRaisesRegex(ValueError, "expert_timeout_invalid"):
                        runtime.configure_provider("openai", "gpt-5.6-terra", "5.00", None, True,
                                                   timeout_seconds=timeout, detailed_capture=False)
                    self.assertEqual(runtime.provider_settings(), baseline)
                    self.assertEqual(runtime.call(lambda workspace: workspace.snapshot()), snapshot)
                    self.assertEqual(len(built), 1)
                # An explicit correction can switch providers without dispatching a call.
                configured = runtime.configure_provider("openai", "gpt-5.6-terra", "5.00", None, False,
                                                        timeout_seconds=300)
                self.assertEqual(configured["selected_provider"], "openai")
                self.assertEqual(configured["timeout_seconds"], 300)
                self.assertTrue(configured["detailed_capture"])
                self.assertEqual(len(built), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
