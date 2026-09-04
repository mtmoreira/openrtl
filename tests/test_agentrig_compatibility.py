from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import tomllib
import unittest

from agentrig.capabilities import McpServerBinding, McpTransport
from agentrig.integrations import CommandTool
from agentrig.integrations.openai import (
    OPENAI_IMAGE_SDK_VERSION,
    OPENAI_RESPONSES_SDK_VERSION,
    OpenAIResponsesClient,
)
import openrtl
from openrtl.adapters import build_command_tools, build_eda_mcp_binding
from tools.validate_public_release import (
    AGENTRIG_VERSION as RELEASE_AGENTRIG_VERSION,
    VERSION as RELEASE_OPENRTL_VERSION,
)


class _LifecycleClient:
    async def create(self, request: object) -> object:
        return request

    async def close(self) -> None:
        return None


class AgentRigCompatibilityTest(unittest.TestCase):
    def test_development_versions_and_lock_are_exact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as source:
            project = tomllib.load(source)["project"]
        with (root / "uv.lock").open("rb") as source:
            locked = tomllib.load(source)["package"]
        packages = {item["name"]: item for item in locked}

        self.assertEqual(openrtl.__version__, "0.4.0")
        self.assertEqual(version("openrtl"), "0.4.0")
        self.assertEqual(version("agentrig"), "0.3.0")
        self.assertEqual(project["version"], "0.4.0")
        self.assertEqual(project["dependencies"], ["agentrig==0.3.0"])
        self.assertEqual(packages["openrtl"]["version"], "0.4.0")
        self.assertEqual(packages["agentrig"]["version"], "0.3.0")
        self.assertEqual(packages["agentrig"]["source"], {"editable": "../agentrig"})

    def test_consumed_public_contracts_remain_available(self) -> None:
        tools = build_command_tools(
            workspace="/private/tmp/openrtl-agentrig-compatibility",
            verilator_executable="/usr/bin/true",
        )
        binding = build_eda_mcp_binding(
            server_id="eda.compatibility",
            command=("/usr/bin/true",),
            allowed_tools=("inspect",),
        )

        self.assertIsInstance(tools.verilator, CommandTool)
        self.assertIsNone(tools.surfer)
        self.assertIsInstance(binding, McpServerBinding)
        self.assertEqual(binding.transport, McpTransport.STDIO)
        self.assertIsInstance(_LifecycleClient(), OpenAIResponsesClient)
        self.assertEqual(OPENAI_RESPONSES_SDK_VERSION, "2.47.0")
        self.assertEqual(OPENAI_IMAGE_SDK_VERSION, "2.47.0")

    def test_published_0_2_0_acceptance_remains_historical(self) -> None:
        self.assertEqual(RELEASE_OPENRTL_VERSION, "0.2.0")
        self.assertEqual(RELEASE_AGENTRIG_VERSION, "0.2.2")


if __name__ == "__main__":
    unittest.main()
