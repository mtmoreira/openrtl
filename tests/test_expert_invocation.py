from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from agentrig.capabilities import (
    CapabilityDescriptor,
    CapabilityFeature,
    CapabilityKind,
    CapabilityLimit,
    DataRetention,
    GenerationUsage,
    ModelMetadata,
    TextGenerationFinishReason,
)
from agentrig.core import CancellationSource, RunContext, RunId, SystemClock, Uuid4IdGenerator
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    ExpertInvocationArtifacts,
    analyze_fifo_waveform,
    invoke_expert_source_edits,
    prepare_expert_source_edit_request,
    propose_fifo_repairs,
)
from openrtl.application import ExpertInvocationPolicy
from openrtl.cli import main


class ControlledExpertInvocationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        repository = Path(__file__).resolve().parents[1]
        self.edit_spec = json.loads(
            (repository / "examples/fifo/faults/level_update_edit_spec.json").read_text(
                encoding="utf-8"
            )
        )
        source_text = (
            repository / "examples/fifo/faults/sync_fifo_level_fault.sv"
        ).read_text(encoding="utf-8")
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault.sv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(source_text, encoding="utf-8")
        trace = self.root / "build/fault/waves.vcd"
        trace.parent.mkdir(parents=True)
        trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")
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
        self.debug_path.write_text(
            json.dumps(debug.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.proposal_path.write_text(
            json.dumps(proposal.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
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
            json.dumps(self.request.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.response = self._response_payload()
        self.policy = ExpertInvocationPolicy(
            "runtime.scripted.expert-edits",
            "scripted.expert-source-edits",
            "scripted",
            "scripted-expert-v1",
            DataRetention.NOT_RETAINED,
        )

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

    def _generator(
        self,
        response: dict[str, Any] | None = None,
        *,
        capability_id: str | None = None,
        provider: str = "scripted",
        model: str = "scripted-expert-v1",
        finish_reason: TextGenerationFinishReason = TextGenerationFinishReason.COMPLETED,
        data_retention: DataRetention = DataRetention.NOT_RETAINED,
        features: frozenset[CapabilityFeature] | None = None,
        max_output_tokens: int | None = None,
    ) -> ScriptedStructuredGenerator[dict[str, Any]]:
        return ScriptedStructuredGenerator(
            descriptor=CapabilityDescriptor(
                capability_id=capability_id or self.policy.capability_id,
                version="1",
                kind=CapabilityKind.STRUCTURED_GENERATION,
                features=(
                    features
                    if features is not None
                    else frozenset({CapabilityFeature.STRUCTURED_OUTPUT})
                ),
                limits={
                    CapabilityLimit.MAX_OUTPUT_TOKENS: (
                        self.policy.max_output_tokens
                        if max_output_tokens is None
                        else max_output_tokens
                    )
                },
                data_retention=data_retention,
            ),
            outcomes=(
                ScriptedStructuredGeneration(
                    encoded_output=response if response is not None else self.response,
                    usage=GenerationUsage(input_tokens=300, output_tokens=100),
                    model=ModelMetadata(provider=provider, model_id=model),
                    finish_reason=finish_reason,
                ),
            ),
        )

    def _context(self) -> RunContext:
        return RunContext.create_root(
            clock=SystemClock(),
            id_generator=Uuid4IdGenerator(RunId),
            cancellation=CancellationSource().token,
        )

    def _invoke(
        self,
        generator: ScriptedStructuredGenerator[dict[str, Any]],
        *,
        policy: ExpertInvocationPolicy | None = None,
    ) -> ExpertInvocationArtifacts:
        return asyncio.run(
            invoke_expert_source_edits(
                self.root,
                request_path=self.request_path,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                generator=generator,
                policy=policy or self.policy,
                context=self._context(),
            )
        )

    def test_scripted_turn_is_bounded_tool_free_and_stays_awaiting_qualification(self) -> None:
        generator = self._generator()

        artifacts = self._invoke(generator)

        self.assertEqual(len(generator.calls), 1)
        prompt = generator.calls[0].request.input.prompt
        self.assertIsNotNone(prompt)
        envelope = json.loads(prompt or "{}")
        self.assertEqual(envelope, artifacts.envelope)
        self.assertEqual(envelope["runtime"]["max_turns"], 1)
        self.assertEqual(envelope["runtime"]["tool_ids"], [])
        self.assertEqual(envelope["constraints"]["tools"], [])
        excerpt_text = "".join(value["text"] for value in envelope["source"]["excerpts"])
        self.assertIn(self.edit_spec["edits"][0]["expected_before"], excerpt_text)
        report = artifacts.report.payload()
        self.assertEqual(report["status"], "awaiting_qualification")
        self.assertFalse(report["provider_output_trusted"])
        self.assertFalse(report["applies_changes"])
        self.assertEqual(report["usage"]["total_tokens"], 400)
        self.assertEqual(artifacts.edit_spec, self.edit_spec)
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_capability_retention_tool_and_model_drift_fail_closed(self) -> None:
        for generator, message in (
            (self._generator(capability_id="scripted.wrong"), "capability identity"),
            (
                self._generator(data_retention=DataRetention.PROVIDER_MANAGED),
                "data retention",
            ),
            (
                self._generator(
                    features=frozenset(
                        {CapabilityFeature.STRUCTURED_OUTPUT, CapabilityFeature.TOOL_USE}
                    )
                ),
                "must not expose tool use",
            ),
            (self._generator(model="wrong-model"), "model identity"),
            (self._generator(max_output_tokens=1), "max_output_tokens"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self._invoke(generator)

    def test_extra_payload_truncation_and_tiny_output_bound_fail_closed(self) -> None:
        response = json.loads(json.dumps(self.response))
        response["hidden_reasoning"] = "not accepted"
        with self.assertRaisesRegex(ValueError, "fields are invalid"):
            self._invoke(self._generator(response))
        with self.assertRaisesRegex(ValueError, "did not complete"):
            self._invoke(
                self._generator(finish_reason=TextGenerationFinishReason.LENGTH)
            )
        tiny_policy = ExpertInvocationPolicy(
            self.policy.runtime_binding_id,
            self.policy.capability_id,
            self.policy.provider,
            self.policy.model,
            self.policy.data_retention,
            max_output_bytes=1,
        )
        with self.assertRaisesRegex(ValueError, "output byte limit"):
            self._invoke(self._generator(), policy=tiny_policy)

    def test_stale_evidence_fails_before_generator_invocation(self) -> None:
        generator = self._generator()
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "// stale\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "source differs from proposal anchors"):
            self._invoke(generator)

        self.assertEqual(generator.calls, ())

    def test_cli_scripted_lane_persists_reviewable_non_applying_artifacts(self) -> None:
        scripted_response = self.root / "build/expert/scripted-response.json"
        scripted_response.write_text(
            json.dumps(self.response, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        arguments = (
            "repair",
            "invoke-expert-source-edits",
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
            "--scripted-response",
            str(scripted_response),
            "--envelope-output",
            "build/invocation/envelope.json",
            "--response-output",
            "build/invocation/response.json",
            "--edit-spec-output",
            "build/invocation/edit-spec.json",
            "--suggestion-report",
            "build/invocation/suggestion.json",
            "--invocation-report",
            "build/invocation/report.json",
        )
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(main(arguments), 0)
        summary = json.loads(captured.getvalue())
        report = json.loads(
            (self.root / "build/invocation/report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["provider"], "scripted")
        self.assertEqual(summary["status"], "awaiting_qualification")
        self.assertEqual(report["runtime"]["data_retention"], "not_retained")
        self.assertEqual(report["runtime"]["tool_ids"], [])
        self.assertFalse(report["applies_changes"])
        self.assertFalse((self.root / "build/application/repaired.sv").exists())


if __name__ == "__main__":
    unittest.main()
