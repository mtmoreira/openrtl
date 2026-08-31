from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    accept_expert_source_edit_output,
    analyze_fifo_waveform,
    draft_source_edit_plan,
    prepare_expert_source_edit_request,
    propose_fifo_repairs,
)
from openrtl.cli import main


class ExpertSourceEditContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        repository = Path(__file__).resolve().parents[1]
        self.external_spec = json.loads(
            (repository / "examples/fifo/faults/level_update_edit_spec.json").read_text(
                encoding="utf-8"
            )
        )
        source_text = (
            repository / "examples/fifo/faults/sync_fifo_level_fault_fixture.sv"
        ).read_text(encoding="utf-8")
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault_fixture.sv"
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
        self.response_path = self.root / "build/expert/response.json"
        self.response_path.write_text(
            json.dumps(self.response_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def response_payload(self) -> dict[str, object]:
        return {
            "applies_changes": False,
            "change_ids": list(self.request.change_ids),
            "context_pack_digest": self.request.context_pack_digest,
            "context_pack_id": self.request.context_pack.pack_id,
            "debug_session_id": self.request.debug_session_id,
            "edits": self.external_spec["edits"],
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

    def test_request_binds_role_context_evidence_and_source_without_invocation(self) -> None:
        payload = self.request.payload()

        self.assertEqual(payload["status"], "awaiting_expert_output")
        self.assertFalse(payload["applies_changes"])
        self.assertEqual(payload["expert_role"], "diagnosis_closure_engineer")
        self.assertEqual(payload["context_pack"]["payload"]["role"], payload["expert_role"])
        self.assertEqual(len(payload["context_pack"]["payload"]["items"]), 3)
        self.assertEqual(payload["source"]["path"], self.source.relative_to(self.root).as_posix())
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_strict_output_becomes_untrusted_awaiting_qualification_spec(self) -> None:
        edit_spec, report = accept_expert_source_edit_output(
            self.root,
            request_path=self.request_path,
            response_path=self.response_path,
        )

        self.assertEqual(edit_spec, self.external_spec)
        self.assertEqual(report.payload()["status"], "awaiting_qualification")
        self.assertFalse(report.payload()["applies_changes"])
        self.assertFalse(report.payload()["trusted"])
        self.assertTrue(report.payload()["next_gate"]["qualification_required"])
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_output_binding_tampering_and_extra_fields_fail_closed(self) -> None:
        for field_name, replacement in (
            ("request_digest", "sha256:" + "0" * 64),
            ("proposal_id", "repair.wrong"),
            ("context_pack_id", "ctx-wrong"),
        ):
            response = self.response_payload()
            response[field_name] = replacement
            self.response_path.write_text(json.dumps(response), encoding="utf-8")
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} binding"):
                    accept_expert_source_edit_output(
                        self.root,
                        request_path=self.request_path,
                        response_path=self.response_path,
                    )
        response = self.response_payload()
        response["provider_payload"] = {"hidden": "not accepted"}
        self.response_path.write_text(json.dumps(response), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fields are invalid"):
            accept_expert_source_edit_output(
                self.root,
                request_path=self.request_path,
                response_path=self.response_path,
            )

    def test_unknown_operations_and_incomplete_change_coverage_fail_closed(self) -> None:
        response = self.response_payload()
        edits = json.loads(json.dumps(response["edits"]))
        edits[0]["operation"] = "execute_script"
        response["edits"] = edits
        self.response_path.write_text(json.dumps(response), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "operation is not allowlisted"):
            accept_expert_source_edit_output(
                self.root,
                request_path=self.request_path,
                response_path=self.response_path,
            )

        response = self.response_payload()
        response["edits"] = []
        self.response_path.write_text(json.dumps(response), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "requires edits"):
            accept_expert_source_edit_output(
                self.root,
                request_path=self.request_path,
                response_path=self.response_path,
            )

    def test_tampered_request_and_stale_source_fail_closed(self) -> None:
        payload = self.request.payload()
        payload["source"]["content_digest"] = "sha256:" + "0" * 64
        self.request_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "request identity is invalid"):
            accept_expert_source_edit_output(
                self.root,
                request_path=self.request_path,
                response_path=self.response_path,
            )

        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "// stale\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "source differs from proposal anchors"):
            prepare_expert_source_edit_request(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
            )

    def test_cli_preserves_separate_expert_and_deterministic_gates(self) -> None:
        request_output = Path("build/cli/request.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    (
                        "repair",
                        "prepare-expert-source-edits",
                        "--root",
                        str(self.root),
                        "--proposal",
                        str(self.proposal_path),
                        "--debug-session",
                        str(self.debug_path),
                        "--source",
                        str(self.source),
                        "--request-output",
                        str(request_output),
                    )
                ),
                0,
            )
        cli_request = json.loads((self.root / request_output).read_text(encoding="utf-8"))
        self.request_path = self.root / request_output
        self.request = prepare_expert_source_edit_request(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
        )
        self.assertEqual(cli_request, self.request.payload())
        self.response_path.write_text(
            json.dumps(self.response_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(
                main(
                    (
                        "repair",
                        "accept-expert-source-edits",
                        "--root",
                        str(self.root),
                        "--request",
                        str(self.request_path),
                        "--response",
                        str(self.response_path),
                        "--edit-spec-output",
                        "build/cli/edit-spec.json",
                        "--suggestion-report",
                        "build/cli/suggestion.json",
                    )
                ),
                0,
            )
        self.assertEqual(json.loads(captured.getvalue())["status"], "awaiting_qualification")
        plan, planning = draft_source_edit_plan(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            edit_spec_path=Path("build/cli/edit-spec.json"),
        )
        self.assertEqual(planning.payload()["status"], "awaiting_review")
        self.assertFalse(planning.payload()["applies_changes"])
        self.assertEqual(plan.proposal_id, self.request.proposal_id)

    def test_cli_outputs_are_idempotent_only_for_exact_bytes(self) -> None:
        arguments = (
            "repair",
            "prepare-expert-source-edits",
            "--root",
            str(self.root),
            "--proposal",
            str(self.proposal_path),
            "--debug-session",
            str(self.debug_path),
            "--source",
            str(self.source),
            "--request-output",
            "build/cli/request.json",
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            self.assertEqual(main(arguments), 0)
        output = self.root / "build/cli/request.json"
        output.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized content"):
            main(arguments)


if __name__ == "__main__":
    unittest.main()
