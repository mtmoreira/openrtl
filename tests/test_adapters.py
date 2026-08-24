from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from agentrig.integrations import DetachedCommandTool
from openrtl.adapters import (
    LogEvent,
    LogLevel,
    ProcessResult,
    SimulationRequest,
    SimulationStatus,
    VcdIndex,
    VerilatorBackend,
    build_command_tools,
    build_eda_mcp_binding,
    parse_jsonl_events,
)


class LogAndWaveformTest(unittest.TestCase):
    def test_log_schema_round_trips_and_vcd_focuses_transitions(self) -> None:
        event = LogEvent(
            timestamp_fs=10_000_000,
            level=LogLevel.INFO,
            component="fifo.scoreboard",
            event="transfer.accepted",
            message="write accepted",
            requirement_ids=("fifo.write",),
            fields={"data": 3},
        )
        self.assertEqual(parse_jsonl_events(event.to_json()), (event,))

        trace = VcdIndex.parse(
            "$timescale 1 ns $end\n"
            "$scope module fifo $end\n"
            "$var wire 1 ! valid $end\n"
            "$upscope $end\n"
            "#0\n0!\n#5\n1!\n#8\n0!\n"
        )
        self.assertEqual(trace.signal_names, ("fifo.valid",))
        focus = trace.focus("runs/run-1/waves.vcd", ("fifo.valid",), 0, 8_000_000)
        self.assertEqual(focus.markers_fs, (0, 5_000_000, 8_000_000))

    def test_invalid_log_reports_exact_line_without_raw_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "line 2"):
            parse_jsonl_events("\nnot-json")


class VerilatorBackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_compile_then_test_success_preserves_exact_commands(self) -> None:
        calls: list[tuple[tuple[str, ...], str, int]] = []

        async def runner(command: tuple[str, ...], cwd: str, timeout: int) -> ProcessResult:
            calls.append((command, cwd, timeout))
            return ProcessResult(0, "ok", "")

        request = SimulationRequest(
            run_id="fifo.run.1",
            top="sync_fifo",
            sources=("examples/fifo/rtl/sync_fifo.sv",),
            test_module="test_sync_fifo",
            build_directory="runs/fifo.run.1/build",
            trace_uri="runs/fifo.run.1/waves.vcd",
            seed=7,
        )
        result = await VerilatorBackend(runner, "/opt/eda/verilator").run(request, "/workspace")

        self.assertEqual(result.status, SimulationStatus.PASSED)
        self.assertEqual(len(calls), 2)
        self.assertIn("--assert", result.compile_command)
        self.assertIn("RANDOM_SEED=7", result.test_command)

    async def test_compile_failure_stops_before_test(self) -> None:
        calls = 0

        async def runner(command: tuple[str, ...], cwd: str, timeout: int) -> ProcessResult:
            nonlocal calls
            del command, cwd, timeout
            calls += 1
            return ProcessResult(1, "", "compile failed")

        request = SimulationRequest(
            run_id="fifo.run.2",
            top="sync_fifo",
            sources=("rtl/sync_fifo.sv",),
            test_module="test_sync_fifo",
            build_directory="runs/fifo.run.2/build",
            trace_uri="runs/fifo.run.2/waves.vcd",
        )
        result = await VerilatorBackend(runner).run(request, "/workspace")
        self.assertEqual(result.status, SimulationStatus.FAILED)
        self.assertEqual(result.failure_signature, "verilator.compile.failed")
        self.assertEqual(calls, 1)


class AgentRigToolBindingTest(unittest.TestCase):
    def test_cli_and_mcp_tool_choices_remain_explicit(self) -> None:
        tools = build_command_tools(
            workspace="/workspace",
            verilator_executable="/opt/eda/verilator",
            surfer_executable="/opt/eda/surfer",
        )
        self.assertEqual(tools.tool_ids, ("eda.verilator", "waveform.surfer"))
        self.assertIsInstance(tools.surfer, DetachedCommandTool)
        binding = build_eda_mcp_binding(
            server_id="local-eda",
            command=("/opt/eda/server", "--stdio"),
            allowed_tools=("lint", "simulate"),
        )
        self.assertEqual(
            binding.tool_ids,
            ("mcp.local-eda.lint", "mcp.local-eda.simulate"),
        )


if __name__ == "__main__":
    unittest.main()
