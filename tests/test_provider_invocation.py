from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
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
    load_expert_provider_invocation_plan,
    prepare_expert_provider_invocation_plan,
    prepare_expert_source_edit_request,
    propose_fifo_repairs,
)
from openrtl.application import ExpertProviderInvocationApproval
from openrtl.cli import main


class _RecordingClient:
    def __init__(self, response: dict[str, Any], model: str) -> None:
        self.response = response
        self.model = model
        self.requests: list[OpenAIResponsesRequest] = []
        self.closed = False

    async def create(self, request: OpenAIResponsesRequest) -> OpenAIResponsesResult:
        self.requests.append(request)
        return OpenAIResponsesResult(
            output_text=json.dumps(self.response),
            model=self.model,
            status=OpenAIResponsesStatus.COMPLETED,
            input_tokens=300,
            output_tokens=100,
        )

    async def close(self) -> None:
        self.closed = True


class _CredentialResolvingFactory:
    def __init__(
        self,
        authentication: OpenAIResponsesAuthenticationSource,
        client: OpenAIResponsesClient,
    ) -> None:
        self.authentication = authentication
        self.client = client
        self.create_count = 0
        self.resolved_lengths: list[int] = []

    def create(self) -> OpenAIResponsesClient:
        self.create_count += 1
        self.resolved_lengths.append(len(self.authentication.resolve_api_key()))
        return self.client


class OptInProviderInvocationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        repository = Path(__file__).resolve().parents[1]
        self.edit_spec = json.loads(
            (repository / "examples/fifo/faults/level_update_edit_spec.json").read_text()
        )
        source_text = (
            repository / "examples/fifo/faults/sync_fifo_level_fault_fixture.sv"
        ).read_text()
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault_fixture.sv"
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
        self.debug_path = self.root / "build/fault/debug-session.json"
        self.proposal_path = self.root / "build/fault/repair-proposal.json"
        self.debug_path.write_text(json.dumps(debug.payload(), indent=2, sort_keys=True) + "\n")
        self.proposal_path.write_text(
            json.dumps(proposal.payload(), indent=2, sort_keys=True) + "\n"
        )
        self.request = prepare_expert_source_edit_request(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
        )
        self.request_path = self.root / "build/expert/request.json"
        self.request_path.parent.mkdir(parents=True)
        self.request_path.write_text(
            json.dumps(self.request.payload(), indent=2, sort_keys=True) + "\n"
        )
        self.plan = prepare_expert_provider_invocation_plan(
            self.root,
            request_path=self.request_path,
            model="test-openai-model",
            credential_environment="OPENRTL_TEST_API_KEY",
        )
        self.plan_path = self.root / "build/expert/provider-plan.json"
        self.plan_path.write_text(json.dumps(self.plan.payload(), indent=2, sort_keys=True) + "\n")
        self.response = self._response_payload()

    def _response_payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "change_ids": list(self.request.change_ids),
            "context_pack_digest": self.request.context_pack_digest,
            "context_pack_id": self.request.context_pack.pack_id,
            "debug_session_id": self.request.debug_session_id,
            "edits": self.edit_spec["edits"],
            "expert_role": "diagnosis_closure_engineer",
            "proposal_id": self.request.proposal_id,
            "request_digest": self.request.content_digest,
            "request_id": self.request.request_id,
            "schema": "openrtl.expert-source-edit-output.v1",
            "source": {
                "content_digest": self.request.source_digest,
                "path": self.request.source_path,
            },
            "status": "proposed_untrusted",
        }

    def _context(self) -> RunContext:
        return RunContext.create_root(
            clock=SystemClock(),
            id_generator=Uuid4IdGenerator(RunId),
            cancellation=CancellationSource().token,
        )

    def _approved_invoke(
        self,
        factory: OpenAIResponsesClientFactory,
        authentication: OpenAIResponsesAuthenticationSource,
    ) -> Any:
        return asyncio.run(
            invoke_approved_openai_expert_source_edits(
                self.root,
                request_path=self.request_path,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                plan_path=self.plan_path,
                approval=ExpertProviderInvocationApproval(
                    self.plan.plan_id,
                    self.plan.content_digest,
                    "Reviewed exact bounded provider plan.",
                ),
                context=self._context(),
                authentication_source=authentication,
                client_factory=factory,
            )
        )

    def test_planning_is_canonical_non_executing_and_awaits_exact_approval(self) -> None:
        payload = self.plan.payload()
        self.assertEqual(payload["status"], "awaiting_explicit_approval")
        self.assertTrue(payload["authorization"]["explicit_digest_required"])
        self.assertEqual(payload["authorization"]["max_provider_calls"], 1)
        self.assertEqual(payload["constraints"]["tools"], [])
        self.assertFalse(payload["constraints"]["applies_changes"])
        self.assertEqual(
            load_expert_provider_invocation_plan(self.root, self.plan_path), self.plan
        )

    def test_approved_call_resolves_credential_late_and_stays_untrusted(self) -> None:
        authentication = EnvironmentOpenAIAuthenticationSource(
            "OPENRTL_TEST_API_KEY",
            environment={"OPENRTL_TEST_API_KEY": "test-value-not-a-real-key"},
        )
        client = _RecordingClient(self.response, self.plan.policy.model)
        factory = _CredentialResolvingFactory(authentication, client)
        self.assertEqual(factory.create_count, 0)

        artifacts = self._approved_invoke(factory, authentication)

        self.assertEqual(factory.create_count, 1)
        self.assertEqual(factory.resolved_lengths, [25])
        self.assertEqual(len(client.requests), 1)
        self.assertTrue(client.closed)
        self.assertEqual(client.requests[0].model, self.plan.policy.model)
        self.assertEqual(artifacts.invocation.report.payload()["status"], "awaiting_qualification")
        provider_report = artifacts.provider_report.payload()
        self.assertEqual(provider_report["status"], "awaiting_qualification")
        self.assertFalse(provider_report["provider_output_trusted"])
        self.assertFalse(provider_report["applies_changes"])
        self.assertEqual(provider_report["authorization"]["provider_call_count"], 1)
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_wrong_approval_and_stale_evidence_fail_before_credentials(self) -> None:
        authentication = EnvironmentOpenAIAuthenticationSource(
            "OPENRTL_TEST_API_KEY",
            environment={"OPENRTL_TEST_API_KEY": "test-value-not-a-real-key"},
        )
        client = _RecordingClient(self.response, self.plan.policy.model)
        factory = _CredentialResolvingFactory(authentication, client)
        wrong = ExpertProviderInvocationApproval(
            self.plan.plan_id,
            "sha256:" + "0" * 64,
            "Wrong digest must fail.",
        )
        with self.assertRaisesRegex(ValueError, "exact plan"):
            asyncio.run(
                invoke_approved_openai_expert_source_edits(
                    self.root,
                    request_path=self.request_path,
                    proposal_path=self.proposal_path,
                    debug_session_path=self.debug_path,
                    source_path=self.source,
                    plan_path=self.plan_path,
                    approval=wrong,
                    context=self._context(),
                    authentication_source=authentication,
                    client_factory=factory,
                )
            )
        self.assertEqual(factory.create_count, 0)

        self.source.write_text(self.source.read_text() + "// stale\n")
        with self.assertRaisesRegex(ValueError, "source differs from proposal anchors"):
            self._approved_invoke(factory, authentication)
        self.assertEqual(factory.create_count, 0)

    def test_tampered_plan_and_missing_cli_opt_in_fail_closed(self) -> None:
        payload = self.plan.payload()
        payload["runtime"]["tool_ids"] = ["shell"]
        self.plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with self.assertRaisesRegex(ValueError, "runtime authority"):
            load_expert_provider_invocation_plan(self.root, self.plan_path)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(
                    (
                        "repair",
                        "invoke-openai-expert-source-edits",
                        "--root",
                        str(self.root),
                        "--request",
                        str(self.request_path),
                        "--proposal",
                        str(self.proposal_path),
                        "--debug-session",
                        str(self.debug_path),
                        "--source",
                        str(self.source),
                        "--plan",
                        str(self.plan_path),
                        "--approve-provider-plan-digest",
                        self.plan.content_digest,
                        "--review-note",
                        "The provider flag remains mandatory.",
                        "--envelope-output",
                        "build/expert/provider-envelope.json",
                        "--response-output",
                        "build/expert/provider-response.json",
                        "--edit-spec-output",
                        "build/expert/provider-edit-spec.json",
                        "--suggestion-report",
                        "build/expert/provider-suggestion.json",
                        "--invocation-report",
                        "build/expert/provider-invocation.json",
                        "--provider-execution-report",
                        "build/expert/provider-execution.json",
                    )
                )

    def test_cli_planning_reads_no_credential_and_writes_no_response(self) -> None:
        output = self.root / "build/expert/cli-provider-plan.json"
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(
                main(
                    (
                        "repair",
                        "plan-expert-provider-invocation",
                        "--root",
                        str(self.root),
                        "--request",
                        str(self.request_path),
                        "--plan-output",
                        str(output),
                        "--model",
                        "test-openai-model",
                        "--credential-environment",
                        "OPENRTL_TEST_API_KEY",
                    )
                ),
                0,
            )
        summary = json.loads(captured.getvalue())
        self.assertEqual(summary["status"], "awaiting_explicit_approval")
        self.assertEqual(summary["content_digest"], self.plan.content_digest)
        self.assertTrue(output.is_file())
        self.assertFalse((self.root / "build/expert/provider-response.json").exists())


if __name__ == "__main__":
    unittest.main()
