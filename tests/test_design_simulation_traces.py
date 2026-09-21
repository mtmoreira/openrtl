"""Synthetic simulation observation: no subprocess, container or provider calls."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from openrtl.adapters.design_simulation import IsolatedDesignSimulator
from openrtl.domain.design_session import JsonObject


class MemoryTraces:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.rows: list[JsonObject] = []

    def status(self) -> JsonObject:
        return {"enabled": self.enabled}

    def record(self, operation_id: str, category: str, payload: JsonObject) -> None:
        self.rows.append({"operation_id": operation_id, "category": category, "payload": payload})

    def records(self, operation_ids: set[str]) -> list[JsonObject]:
        return [row for row in self.rows if row["operation_id"] in operation_ids]


class SimulationTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # Existing recovery/tests may construct adapters without invoking profile validation.
        self.simulator = object.__new__(IsolatedDesignSimulator)
        self.simulator.project = self.root
        self.simulator.workload_transport = None
        self.simulator.profile = {"schema": "openrtl.design-container.v1",
            "docker_executable": "/unit-only/docker", "socket": "/unit-only/socket",
            "image_id": "sha256:" + "a" * 64, "python_executable": "/unit-only/python",
            "verilator_version": "5.046", "timeout_seconds": 120}

    def invocation(self, argv: list[str] | None = None):
        async def run(files: dict[str, str], manifest: JsonObject,
                      digest: str, operation_id: str) -> JsonObject:
            code, output = await self.simulator._process(argv or ["/unit-only/docker", "info"],
                                                         self.root, 7, 1024)
            return {"exit_code": code, "output": output.decode()}
        return run

    def test_capture_defaults_off_and_does_not_change_execution_arguments(self) -> None:
        execute = AsyncMock(return_value=(9, b"synthetic merged output"))
        with patch.object(self.simulator, "_simulate", side_effect=self.invocation()), \
             patch.object(self.simulator, "_execute_process", execute):
            result = asyncio.run(self.simulator.simulate({}, {}, "sha256:" + "b" * 64, "c" * 32))
        self.assertEqual(result, {"exit_code": 9, "output": "synthetic merged output"})
        execute.assert_awaited_once_with(["/unit-only/docker", "info"], self.root, 7, 1024)
        self.assertIsNone(self.simulator._trace_context.get())

    def test_captures_actual_process_arguments_output_and_runtime_with_shared_redaction(self) -> None:
        traces = self.simulator.trace_store = MemoryTraces()
        argv = ["/unit-only/tool", "--api-key", "synthetic-secret", "--mode=read"]
        execute = AsyncMock(return_value=(4, b"compile failed\npassword=synthetic-password\n"))
        with patch.object(self.simulator, "_simulate", side_effect=self.invocation(argv)), \
             patch.object(self.simulator, "_execute_process", execute):
            result = asyncio.run(self.simulator.simulate({}, {}, "digest", "d" * 32))
        execute.assert_awaited_once_with(argv, self.root, 7, 1024)
        self.assertIn("synthetic-password", result["output"])
        self.assertEqual(len(traces.rows), 2)
        request, response = [row["payload"] for row in traces.rows]
        self.assertEqual(request["kind"], "process.request")
        self.assertEqual(response["kind"], "process.response")
        self.assertEqual(request["run_id"], response["run_id"])
        self.assertEqual(request["correlation"], response["correlation"])
        self.assertEqual(request["correlation"]["operation_id"], "d" * 32)
        self.assertTrue(request["correlation"]["process_id"])
        details, output = json.loads(request["content"]), json.loads(response["content"])
        self.assertEqual(details["cwd"], str(self.root))
        self.assertEqual(details["argv"][0], argv[0])
        self.assertEqual(details["environment"], "not captured")
        self.assertFalse(details["shell"])
        self.assertEqual(output["exit_code"], 4)
        self.assertEqual(output["stderr_mode"], "merged_into_stdout")
        self.assertIsNone(output["stderr"])
        self.assertIn("compile failed", output["stdout"])
        self.assertGreaterEqual(output["elapsed_ms"], 0)
        self.assertEqual(output["output_bytes"], len(execute.return_value[1]))
        self.assertNotIn("synthetic-secret", json.dumps(traces.rows))
        self.assertNotIn("synthetic-password", json.dumps(traces.rows))

    def test_failure_and_cancellation_preserve_original_exception_without_raw_trace_text(self) -> None:
        for error, code in ((TimeoutError("synthetic-private-exception"), "simulation_process_timeout"),
                            (ValueError("synthetic-private-exception"), "simulation_process_failed"),
                            (asyncio.CancelledError("synthetic-private-exception"), "simulation_process_cancelled")):
            with self.subTest(code=code):
                traces = self.simulator.trace_store = MemoryTraces()
                with patch.object(self.simulator, "_simulate", side_effect=self.invocation()), \
                     patch.object(self.simulator, "_execute_process", AsyncMock(side_effect=error)):
                    with self.assertRaises(type(error)) as raised:
                        asyncio.run(self.simulator.simulate({}, {}, "digest", "e" * 32))
                self.assertIs(raised.exception, error)
                response = json.loads(traces.rows[-1]["payload"]["content"])
                self.assertEqual(response["failure_code"], code)
                self.assertIsNone(response["exit_code"])
                self.assertIsNone(response["stdout"])
                self.assertNotIn("synthetic-private-exception", json.dumps(traces.rows))
                self.assertIsNone(self.simulator._trace_context.get())

    def test_capture_bounds_do_not_truncate_actual_process_return(self) -> None:
        traces = self.simulator.trace_store = MemoryTraces()
        output = b"x" * (300 * 1024)
        with patch.object(self.simulator, "_simulate", side_effect=self.invocation()), \
             patch.object(self.simulator, "_execute_process", AsyncMock(return_value=(0, output))):
            result = asyncio.run(self.simulator.simulate({}, {}, "digest", "f" * 32))
        self.assertEqual(result["output"].encode(), output)
        response = traces.rows[-1]["payload"]
        self.assertTrue(response["truncated"])
        self.assertGreater(response["content_bytes"], 262144)
        self.assertLessEqual(len(response["content"].encode()), 262144)

    def test_concurrent_calls_keep_operation_contexts_separate_and_restore_afterwards(self) -> None:
        traces = self.simulator.trace_store = MemoryTraces()

        async def execute(*args: object) -> tuple[int, bytes]:
            await asyncio.sleep(0)
            return 0, b"synthetic"

        async def run() -> None:
            await asyncio.gather(self.simulator.simulate({}, {}, "digest", "1" * 32),
                                 self.simulator.simulate({}, {}, "digest", "2" * 32))
            await self.simulator._process(["/unit-only/tool"], self.root, 7, 1024)

        with patch.object(self.simulator, "_simulate", side_effect=self.invocation()), \
             patch.object(self.simulator, "_execute_process", side_effect=execute):
            asyncio.run(run())
        self.assertEqual(len(traces.rows), 4)
        process_ids = set()
        for operation_id in ("1" * 32, "2" * 32):
            rows = traces.records({operation_id})
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["payload"]["run_id"], rows[1]["payload"]["run_id"])
            self.assertEqual(rows[0]["payload"]["correlation"]["operation_id"], operation_id)
            process_ids.add(rows[0]["payload"]["correlation"]["process_id"])
        self.assertEqual(len(process_ids), 2)

    def test_decodes_existing_runner_log_without_inventing_guest_or_inner_process_details(self) -> None:
        traces = self.simulator.trace_store = MemoryTraces()
        log = b"synthetic compiler output\npassword=synthetic-secret\n"
        encoded = base64.b64encode(log).decode()
        payload = json.dumps({"status": "failed", "model_tests": 0,
            "artifacts": {"runner.log": encoded}, "error_code": "isolated_tests_or_build_failed"}).encode()
        execute = AsyncMock(side_effect=[(0, b"c" * 64), (0, payload), (0, b"c" * 64)])
        with patch.object(self.simulator, "_execute_process", execute):
            report = asyncio.run(self.simulator.simulate({"rtl/top.sv": "module top; endmodule"}, {},
                                                         "sha256:" + "b" * 64, "3" * 32))
        self.assertEqual(execute.await_count, 3)
        self.assertEqual(report["status"], "failed")
        self.assertEqual((self.root / "runs" / ("3" * 32) / "evidence" / "runner.log").read_bytes(), log)
        contents = [json.loads(row["payload"]["content"]) for row in traces.rows]
        logs = [row for row in contents if row.get("evidence_kind") == "decoded_runner_log"]
        self.assertEqual(len(logs), 1)
        self.assertIn("synthetic compiler output", logs[0]["stdout"])
        self.assertEqual(logs[0]["artifact"]["path"], report["artifacts"]["runner.log"]["path"])
        self.assertEqual(logs[0]["inner_process_argv"], "unavailable")
        self.assertEqual(logs[0]["guest_transport"], "not instrumented")
        self.assertIsNone(logs[0]["exit_code"])
        self.assertIsNone(logs[0]["elapsed_ms"])
        self.assertEqual(sum(row.get("encoded_artifact_bodies_omitted", False) for row in contents), 1)
        self.assertNotIn(encoded, json.dumps(traces.rows))
        self.assertNotIn("synthetic-secret", json.dumps(traces.rows))


if __name__ == "__main__":
    unittest.main()
