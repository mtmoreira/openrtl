"""AgentRig bindings for user-selected OpenRTL CLI and MCP tools."""

from __future__ import annotations

from dataclasses import dataclass

from agentrig.capabilities import McpServerBinding, McpTransport
from agentrig.core import EffectProfile
from agentrig.integrations import CommandTool


@dataclass(frozen=True)
class OpenRTLCommandTools:
    verilator: CommandTool
    surfer: CommandTool | None

    @property
    def tool_ids(self) -> tuple[str, ...]:
        values = [self.verilator.contract.tool_id]
        if self.surfer is not None:
            values.append(self.surfer.contract.tool_id)
        return tuple(values)


def build_command_tools(
    *,
    workspace: str,
    verilator_executable: str,
    surfer_executable: str | None = None,
) -> OpenRTLCommandTools:
    verilator = CommandTool(
        tool_id="eda.verilator",
        version="1",
        purpose="Compile, lint, or simulate explicitly selected SystemVerilog sources.",
        executable=verilator_executable,
        working_directory=workspace,
        effect_profile=EffectProfile.IDEMPOTENT,
        timeout_seconds=300,
        max_output_bytes=2_000_000,
    )
    surfer = (
        CommandTool(
            tool_id="waveform.surfer",
            version="1",
            purpose="Open an explicitly selected waveform focus in Surfer.",
            executable=surfer_executable,
            working_directory=workspace,
            effect_profile=EffectProfile.NON_REPEATABLE,
            timeout_seconds=30,
            max_output_bytes=100_000,
        )
        if surfer_executable is not None
        else None
    )
    return OpenRTLCommandTools(verilator, surfer)


def build_eda_mcp_binding(
    *,
    server_id: str,
    command: tuple[str, ...],
    allowed_tools: tuple[str, ...],
    environment_variables: tuple[str, ...] = (),
) -> McpServerBinding:
    """Create an exact stdio EDA MCP binding without resolving credentials."""
    return McpServerBinding(
        server_id=server_id,
        transport=McpTransport.STDIO,
        command=command,
        allowed_tools=allowed_tools,
        environment_variables=environment_variables,
    )
