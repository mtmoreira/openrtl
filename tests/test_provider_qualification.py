from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from agentrig.core import CancellationSource, RunContext, RunId, SystemClock, Uuid4IdGenerator
from agentrig.integrations.openai import (
    OpenAIResponsesAuthenticationSource,
    OpenAIResponsesClient,
    OpenAIResponsesClientFactory,
    OpenAIResponsesRequest,
    OpenAIResponsesResult,
    OpenAIResponsesStatus,
)
from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    EnvironmentOpenAIAuthenticationSource,
    analyze_fifo_waveform,
    invoke_approved_openai_expert_source_edits,
    prepare_expert_provider_invocation_plan,
    prepare_expert_source_edit_request,
    propose_fifo_repairs,
    qualify_provider_source_edits,
)
from openrtl.application import ExpertProviderInvocationApproval
from openrtl.cli import main


class _Client:
    def __init__(self, response: dict[str, Any], model: str) -> None:
        self.response = response
        self.model = model

    async def create(self, request: OpenAIResponsesRequest) -> OpenAIResponsesResult:
        return OpenAIResponsesResult(
            output_text=json.dumps(self.response),
            model=self.model,
            status=OpenAIResponsesStatus.COMPLETED,
            input_tokens=300,
            output_tokens=100,
        )

    async def close(self) -> None:
        return None


class _Factory:
    def __init__(
        self,
        authentication: OpenAIResponsesAuthenticationSource,
        client: OpenAIResponsesClient,
    ) -> None:
        self.authentication = authentication
        self.client = client
        self.calls = 0

    def create(self) -> OpenAIResponsesClient:
        self.authentication.resolve_api_key()
        self.calls += 1
        return self.client


class ProviderOutputQualificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        repository = Path(__file__).resolve().parents[1]
        source_text = (
            repository / "examples/fifo/faults/sync_fifo_level_fault.sv"
        ).read_text()
        edit_spec = json.loads(
            (repository / "examples/fifo/faults/level_update_edit_spec.json").read_text()
        )
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault.sv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(source_text)
        trace = self.root / "build/fault/waves.vcd"
        trace.parent.mkdir(parents=True)
        trace.write_text(render_fifo_trace(level_update_fault=True))
        debug = analyze_fifo_waveform(
            self.root,
            trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
            rtl_path=self.source.relative_to(self.root),
        )
        proposal = propose_fifo_repairs(debug, report_uri="build/fault/debug-session.json")
        self.debug_path = self._write("build/fault/debug-session.json", debug.payload())
        self.proposal_path = self._write("build/fault/proposal.json", proposal.payload())
        request = prepare_expert_source_edit_request(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
        )
        self.request_path = self._write("build/provider/request.json", request.payload())
        plan = prepare_expert_provider_invocation_plan(
            self.root,
            request_path=self.request_path,
            model="synthetic-openai-model",
            credential_environment="OPENRTL_SYNTHETIC_API_KEY",
        )
        self.plan_path = self._write("build/provider/plan.json", plan.payload())
        response = {
            "applies_changes": False,
            "change_ids": list(request.change_ids),
            "context_pack_digest": request.context_pack_digest,
            "context_pack_id": request.context_pack.pack_id,
            "debug_session_id": request.debug_session_id,
            "edits": edit_spec["edits"],
            "expert_role": "diagnosis_closure_engineer",
            "proposal_id": request.proposal_id,
            "request_digest": request.content_digest,
            "request_id": request.request_id,
            "schema": "openrtl.expert-source-edit-output.v1",
            "source": {
                "content_digest": request.source_digest,
                "path": request.source_path,
            },
            "status": "proposed_untrusted",
        }
        authentication = EnvironmentOpenAIAuthenticationSource(
            "OPENRTL_SYNTHETIC_API_KEY",
            environment={"OPENRTL_SYNTHETIC_API_KEY": "synthetic-test-value"},
        )
        factory = _Factory(authentication, _Client(response, plan.policy.model))
        artifacts = asyncio.run(
            invoke_approved_openai_expert_source_edits(
                self.root,
                request_path=self.request_path,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                plan_path=self.plan_path,
                approval=ExpertProviderInvocationApproval(
                    plan.plan_id,
                    plan.content_digest,
                    "Reviewed synthetic provider plan.",
                ),
                context=RunContext.create_root(
                    clock=SystemClock(),
                    id_generator=Uuid4IdGenerator(RunId),
                    cancellation=CancellationSource().token,
                ),
                authentication_source=authentication,
                client_factory=factory,
            )
        )
        self.assertEqual(factory.calls, 1)
        self.execution_path = self._write(
            "build/provider/execution.json",
            artifacts.provider_report.payload(),
        )
        self.invocation_path = self._write(
            "build/provider/invocation.json",
            artifacts.invocation.report.payload(),
        )
        self.suggestion_path = self._write(
            "build/provider/suggestion.json",
            artifacts.invocation.suggestion,
        )
        self.edit_spec_path = self._write(
            "build/provider/edit-spec.json",
            artifacts.invocation.edit_spec,
        )

    def _write(self, relative: str, payload: dict[str, Any]) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def _qualify(self) -> Any:
        return qualify_provider_source_edits(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            provider_plan_path=self.plan_path,
            provider_execution_report_path=self.execution_path,
            invocation_report_path=self.invocation_path,
            suggestion_report_path=self.suggestion_path,
            edit_spec_path=self.edit_spec_path,
        )

    def test_exact_provider_lineage_qualifies_only_to_review(self) -> None:
        edit_plan, planning, qualification = self._qualify()
        payload = qualification.payload()
        self.assertEqual(payload["status"], "awaiting_review")
        self.assertFalse(payload["applies_changes"])
        self.assertFalse(payload["provider_output_trusted"])
        self.assertTrue(payload["next_gate"]["human_review_required"])
        self.assertEqual(payload["edit_plan"]["content_digest"], edit_plan.content_digest)
        self.assertEqual(payload["planning"]["planning_id"], planning.planning_id)
        self.assertFalse((self.root / "build/provider/candidate.sv").exists())

    def test_cross_run_and_tampered_artifacts_fail_closed(self) -> None:
        original_execution = json.loads(self.execution_path.read_text())
        tampered_execution = json.loads(self.execution_path.read_text())
        tampered_execution["invocation"]["invocation_id"] += ".other"
        self._write("build/provider/execution.json", tampered_execution)
        with self.assertRaisesRegex(ValueError, "invocation report lineage"):
            self._qualify()

        self._write("build/provider/execution.json", original_execution)
        original_suggestion = json.loads(self.suggestion_path.read_text())
        tampered_suggestion = json.loads(self.suggestion_path.read_text())
        tampered_suggestion["edit_spec_digest"] = "sha256:" + "0" * 64
        self._write("build/provider/suggestion.json", tampered_suggestion)
        with self.assertRaisesRegex(ValueError, "suggestion report lineage"):
            self._qualify()
        self._write("build/provider/suggestion.json", original_suggestion)

        tampered_spec = json.loads(self.edit_spec_path.read_text())
        tampered_spec["edits"][0]["replacement"] += " "
        self._write("build/provider/edit-spec.json", tampered_spec)
        with self.assertRaisesRegex(ValueError, "suggestion report lineage"):
            self._qualify()

    def test_stale_source_fails_before_review_receipt(self) -> None:
        self.source.write_text(self.source.read_text() + "// stale\n")
        with self.assertRaisesRegex(ValueError, "source anchor"):
            self._qualify()

    def test_cli_writes_three_unique_non_applying_outputs(self) -> None:
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(
                main(
                    (
                        "repair",
                        "qualify-provider-source-edits",
                        "--root",
                        str(self.root),
                        "--proposal",
                        str(self.proposal_path),
                        "--debug-session",
                        str(self.debug_path),
                        "--source",
                        str(self.source),
                        "--provider-plan",
                        str(self.plan_path),
                        "--provider-execution-report",
                        str(self.execution_path),
                        "--invocation-report",
                        str(self.invocation_path),
                        "--suggestion-report",
                        str(self.suggestion_path),
                        "--edit-spec",
                        str(self.edit_spec_path),
                        "--edit-plan-output",
                        "build/qualified/edit-plan.json",
                        "--planning-report",
                        "build/qualified/planning.json",
                        "--qualification-report",
                        "build/qualified/qualification.json",
                    )
                ),
                0,
            )
        summary = json.loads(captured.getvalue())
        self.assertEqual(summary["status"], "awaiting_review")
        self.assertFalse(summary["applies_changes"])
        self.assertFalse(summary["provider_output_trusted"])
        self.assertTrue((self.root / "build/qualified/edit-plan.json").is_file())
        self.assertTrue((self.root / "build/qualified/planning.json").is_file())
        self.assertTrue((self.root / "build/qualified/qualification.json").is_file())
        self.assertFalse((self.root / "build/provider/candidate.sv").exists())


if __name__ == "__main__":
    unittest.main()
