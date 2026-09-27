"""Synthetic generation diagnostics, with no provider or generated-code execution."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_sessions import inspect_session
from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_diagnostics import safe_event
from openrtl.domain.artifact_validation import ARTIFACT_VALIDATION_CODES, artifact_validation_code
from openrtl.domain.design_session import JsonObject, canonical, content_digest
from tests.test_design_agent import FakeExpert, specification


class SyntheticTraceRecorder:
    def __init__(self) -> None:
        self.saved: list[JsonObject] = []

    def record(self, operation_id: str, category: str, payload: JsonObject) -> None:
        self.saved.append({"operation_id": operation_id, "category": category,
                           "payload": copy.deepcopy(payload)})

    def records(self, operation_ids: set[str]) -> list[JsonObject]:
        return [row for row in self.saved if row["operation_id"] in operation_ids]

    def status(self) -> JsonObject:
        return {"enabled": True}


class ArtifactValidationCodeTest(unittest.TestCase):
    def test_only_exact_local_value_errors_expose_an_allowlisted_rule(self) -> None:
        for code in ARTIFACT_VALIDATION_CODES:
            with self.subTest(code=code):
                self.assertEqual(artifact_validation_code(ValueError(code)), code)

        class PrivateError(ValueError):
            def __str__(self) -> str:
                raise AssertionError("Exception formatting must not be called")

        class PrivateText(str):
            def __hash__(self) -> int:
                raise AssertionError("Untrusted string hashing must not be called")

        errors = (
            ValueError(), ValueError("source_path_invalid", "SYNTHETIC_PRIVATE"),
            ValueError("source_path_invalid: SYNTHETIC_PRIVATE"), ValueError(["source_path_invalid"]),
            ValueError(PrivateText("source_path_invalid")), PrivateError("source_path_invalid"),
            KeyError("source_path_invalid"), TypeError("source_path_invalid"),
        )
        for index, error in enumerate(errors):
            with self.subTest(case=index):
                self.assertIsNone(artifact_validation_code(error))

    def test_safe_history_projects_only_known_string_rules(self) -> None:
        for code in (*ARTIFACT_VALIDATION_CODES, "port_width_invalid"):
            with self.subTest(code=code):
                row = {"sequence": 1, "event": "operation.failed", "fields": {
                    "error_code": "expert_output_invalid", "validation_code": code}}
                self.assertEqual(safe_event(row)["fields"]["validation_code"], code)
        for value in ("SYNTHETIC_PRIVATE", "source_path_invalid: private", ["source_path_invalid"],
                      {"code": "source_path_invalid"}, None, 12):
            row = {"sequence": 1, "event": "operation.failed", "fields": {
                "error_code": "expert_output_invalid", "validation_code": value}}
            self.assertEqual(safe_event(row)["fields"], {"error_code": "expert_output_invalid"})


class ArtifactFailureDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = DesignSessionStore(Path(temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.trace = SyntheticTraceRecorder()
        self.agent = DesignAgent(self.store, self.expert, trace_store=self.trace)
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        asyncio.run(self.agent.advance())
        asyncio.run(self.agent.advance())

    def test_captured_path_shape_preserves_received_usage_state_and_private_capture(self) -> None:
        # Match only the captured path/type structure, not any private source.
        paths = ("model/__init__.py", "model/sync_fifo.py",
                 "test_model/__init__.py", "test_model/test_sync_fifo.py")
        output = {"summary": "Synthetic model contribution", "manifest": None,
                  "files": [{"path": path, "content": "# SYNTHETIC_PRIVATE_SOURCE\n"} for path in paths]}
        self.expert.responses["reference_model"] = output
        before = self.store.read()
        files = self.store.contents(before)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(self.agent.advance())
        failed = self.store.read()
        self.assertEqual(failed["stage"], 2)
        self.assertEqual(failed["calls"], before["calls"] + 1)
        self.assertIsNone(failed["active"])
        self.assertEqual(failed["last_error"], "expert_output_invalid")
        for field in ("files", "summaries", "approved_spec", "status", "manifest", "simulation", "provider"):
            self.assertEqual(failed[field], before[field])
        self.assertEqual(self.store.contents(failed), files)
        received, failure = self.store.events()[-2:]
        self.assertEqual(received["event"], "operation.received")
        self.assertEqual(received["fields"]["input_tokens"], 10)
        self.assertEqual(received["fields"]["output_tokens"], 20)
        self.assertIs(type(received["fields"]["elapsed_ms"]), int)
        self.assertEqual(failure["event"], "operation.failed")
        self.assertEqual(failure["fields"]["error_code"], "expert_output_invalid")
        self.assertEqual(failure["fields"]["validation_code"], "source_path_invalid")
        self.assertEqual(received["fields"]["operation_id"], failure["fields"]["operation_id"])
        self.assertEqual(self.trace.saved[-1]["category"], "assistant_output")
        self.assertEqual(self.trace.saved[-1]["payload"]["output"], output)
        self.assertEqual(len(self.expert.seen), 3)  # No implicit correction call.
        report = inspect_session(self.store)
        self.assertEqual(report["events"][-1]["fields"]["validation_code"], "source_path_invalid")
        safe_bytes = canonical({"state": failed, "events": self.store.events(), "report": report})
        self.assertNotIn(b"SYNTHETIC_PRIVATE_SOURCE", safe_bytes)
        self.assertNotIn(b"test_model/", safe_bytes)

        # A separately requested retry retains prior files and advances once.
        del self.expert.responses["reference_model"]
        recovered = asyncio.run(self.agent.advance())
        self.assertEqual(recovered["stage"], 3)
        self.assertEqual(recovered["calls"], before["calls"] + 2)
        self.assertIsNone(recovered["last_error"])
        self.assertEqual({path: self.store.contents(recovered)[path] for path in files}, files)
        self.assertEqual(len(self.expert.seen), 4)

    def test_untrusted_validation_exceptions_remain_generic_after_metering(self) -> None:
        class PrivateValueError(ValueError):
            def __str__(self) -> str:
                raise AssertionError("Private exceptions must not be rendered")

        errors = (ValueError("SYNTHETIC_PRIVATE"), KeyError("source_path_invalid"),
                  TypeError("source_path_invalid"), PrivateValueError("source_path_invalid"),
                  ValueError("source_path_invalid", "SYNTHETIC_PRIVATE"))
        before = self.store.read()
        for index, error in enumerate(errors):
            with self.subTest(case=index), patch(
                    "openrtl.application.design_agent.validate_files", side_effect=error):
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(self.agent.advance())
            received, failure = self.store.events()[-2:]
            self.assertEqual(received["event"], "operation.received")
            self.assertEqual(received["fields"]["output_tokens"], 20)
            self.assertEqual(failure["fields"]["error_code"], "expert_output_invalid")
            self.assertNotIn("validation_code", failure["fields"])
            self.assertEqual(self.store.read()["files"], before["files"])
            self.assertEqual(self.store.read()["stage"], 2)
        self.assertNotIn(b"SYNTHETIC_PRIVATE", canonical(self.store.events()))


if __name__ == "__main__":
    unittest.main()
