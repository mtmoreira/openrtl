"""Local OpenRTL CLI with an explicit opt-in boundary for provider calls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Sequence

from agentrig.capabilities import (
    CapabilityDescriptor,
    CapabilityFeature,
    CapabilityKind,
    CapabilityLimit,
    DataRetention,
    GenerationUsage,
    ModelMetadata,
    TextGenerationFinishReason,
    ToolInvocation,
)
from agentrig.core import (
    CancellationSource,
    RunContext,
    RunId,
    SystemClock,
    Uuid4IdGenerator,
)
from agentrig.integrations import CommandInput
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters import (
    LocalDesignCatalog,
    EnvironmentOpenAIAuthenticationSource,
    accept_expert_source_edit_output,
    analyze_fifo_waveform,
    apply_reviewed_source_edits,
    build_surfer_tool,
    draft_source_edit_plan,
    fifo_repair_focus,
    inspect_vcd,
    invoke_expert_source_edits,
    invoke_approved_openai_expert_source_edits,
    load_expert_provider_invocation_plan,
    load_fifo_canary_evidence,
    load_source_edit_plan,
    prepare_expert_source_edit_request,
    prepare_expert_provider_invocation_plan,
    propose_fifo_repairs,
    surfer_command_file,
)
from openrtl.application import (
    EXPERT_DEFINITIONS,
    ExpertInvocationPolicy,
    ExpertProviderInvocationApproval,
    FIFO_RUN_REF,
    FIFO_SOURCE_REFS,
    OpenRTLWorkflow,
    RepairApproval,
    run_scripted_fifo,
)
from openrtl.domain import InteractionMode


_CANARY_FILES = (
    "examples/fifo/spec.md",
    "examples/fifo/model.py",
    "examples/fifo/test_model.py",
    "examples/fifo/rtl/sync_fifo.sv",
    "examples/fifo/dv/Makefile",
    "examples/fifo/dv/test_sync_fifo.py",
)
_FIFO_REQUIREMENTS = (
    "fifo.reset",
    "fifo.write",
    "fifo.read",
    "fifo.order",
    "fifo.backpressure",
    "fifo.simultaneous",
    "fifo.wrap",
    "fifo.status",
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="openrtl")
    subcommands = root.add_subparsers(dest="command", required=True)
    subcommands.add_parser("experts", help="list stable expert contracts")
    plan = subcommands.add_parser("plan", help="show the deterministic V1 workflow")
    plan.add_argument("--mode", choices=("build", "learn"), default="build")
    canary = subcommands.add_parser("canary", help="validate FIFO collateral structure")
    canary.add_argument("--root", type=Path, default=Path.cwd())
    catalog = subcommands.add_parser("catalog", help="list local reusable designs")
    catalog.add_argument("--root", type=Path, required=True)
    verified = subcommands.add_parser(
        "verified-canary",
        help="build FIFO package candidacy from retained Verilator evidence",
    )
    verified.add_argument("--root", type=Path, default=Path.cwd())
    verified.add_argument(
        "--manifest",
        type=Path,
        default=Path("build/verilator-fifo-canary/evidence.json"),
    )
    verified.add_argument("--mode", choices=("build", "learn"), default="build")
    waveform = subcommands.add_parser(
        "waveform",
        help="inspect VCD traces and prepare an explicit Surfer focus",
    )
    waveform_commands = waveform.add_subparsers(
        dest="waveform_command",
        required=True,
    )
    inspect = waveform_commands.add_parser(
        "inspect",
        help="list signals or inspect bounded transitions",
    )
    _add_waveform_selection_arguments(inspect)
    inspect.add_argument("--output", type=Path)
    focus = waveform_commands.add_parser(
        "focus",
        help="write inspection JSON and a deterministic Surfer command file",
    )
    _add_waveform_selection_arguments(focus)
    focus.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/waveform-focus"),
    )
    focus.add_argument("--surfer-executable", type=Path)
    focus.add_argument("--launch", action="store_true")
    diagnose_fifo = waveform_commands.add_parser(
        "diagnose-fifo",
        help="explain FIFO clock-edge behavior and flag invariant violations",
    )
    _add_fifo_debug_arguments(diagnose_fifo)
    diagnose_fifo.add_argument("--output", type=Path)
    propose_fifo = waveform_commands.add_parser(
        "propose-fifo-repair",
        help="derive a reviewable non-applying repair proposal from FIFO findings",
    )
    _add_fifo_debug_arguments(propose_fifo)
    propose_fifo.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/fifo-repair-proposal"),
    )
    repair = subcommands.add_parser(
        "repair",
        help="draft or apply evidence-bound source edits",
    )
    repair_commands = repair.add_subparsers(dest="repair_command", required=True)
    prepare_expert_edits = repair_commands.add_parser(
        "prepare-expert-source-edits",
        help="prepare a provider-neutral, evidence-bound expert output request",
    )
    prepare_expert_edits.add_argument("--root", type=Path, default=Path.cwd())
    prepare_expert_edits.add_argument("--proposal", type=Path, required=True)
    prepare_expert_edits.add_argument("--debug-session", type=Path, required=True)
    prepare_expert_edits.add_argument("--source", type=Path, required=True)
    prepare_expert_edits.add_argument("--request-output", type=Path, required=True)
    accept_expert_edits = repair_commands.add_parser(
        "accept-expert-source-edits",
        help="ingest strict expert output as an untrusted specification candidate",
    )
    accept_expert_edits.add_argument("--root", type=Path, default=Path.cwd())
    accept_expert_edits.add_argument("--request", type=Path, required=True)
    accept_expert_edits.add_argument("--response", type=Path, required=True)
    accept_expert_edits.add_argument("--edit-spec-output", type=Path, required=True)
    accept_expert_edits.add_argument("--suggestion-report", type=Path, required=True)
    invoke_expert_edits = repair_commands.add_parser(
        "invoke-expert-source-edits",
        help="run one bounded provider-free scripted expert turn",
    )
    invoke_expert_edits.add_argument("--root", type=Path, default=Path.cwd())
    invoke_expert_edits.add_argument("--request", type=Path, required=True)
    invoke_expert_edits.add_argument("--proposal", type=Path, required=True)
    invoke_expert_edits.add_argument("--debug-session", type=Path, required=True)
    invoke_expert_edits.add_argument("--source", type=Path, required=True)
    invoke_expert_edits.add_argument("--scripted-response", type=Path, required=True)
    invoke_expert_edits.add_argument("--envelope-output", type=Path, required=True)
    invoke_expert_edits.add_argument("--response-output", type=Path, required=True)
    invoke_expert_edits.add_argument("--edit-spec-output", type=Path, required=True)
    invoke_expert_edits.add_argument("--suggestion-report", type=Path, required=True)
    invoke_expert_edits.add_argument("--invocation-report", type=Path, required=True)
    invoke_expert_edits.add_argument(
        "--runtime-binding-id",
        default="runtime.scripted.expert-edits",
    )
    invoke_expert_edits.add_argument(
        "--capability-id",
        default="scripted.expert-source-edits",
    )
    invoke_expert_edits.add_argument("--model", default="scripted-expert-v1")
    invoke_expert_edits.add_argument("--timeout-seconds", type=int, default=120)
    invoke_expert_edits.add_argument("--max-input-bytes", type=int, default=64 * 1024)
    invoke_expert_edits.add_argument("--max-output-bytes", type=int, default=64 * 1024)
    invoke_expert_edits.add_argument("--max-output-tokens", type=int, default=4096)
    plan_provider_invocation = repair_commands.add_parser(
        "plan-expert-provider-invocation",
        help="prepare a non-executing OpenAI Responses invocation plan",
    )
    plan_provider_invocation.add_argument("--root", type=Path, default=Path.cwd())
    plan_provider_invocation.add_argument("--request", type=Path, required=True)
    plan_provider_invocation.add_argument("--plan-output", type=Path, required=True)
    plan_provider_invocation.add_argument("--model", required=True)
    plan_provider_invocation.add_argument(
        "--credential-environment",
        required=True,
        help="environment-variable name only; the value is not read while planning",
    )
    plan_provider_invocation.add_argument("--timeout-seconds", type=int, default=120)
    plan_provider_invocation.add_argument(
        "--max-input-bytes", type=int, default=64 * 1024
    )
    plan_provider_invocation.add_argument(
        "--max-output-bytes", type=int, default=64 * 1024
    )
    plan_provider_invocation.add_argument(
        "--max-output-tokens", type=int, default=4096
    )
    invoke_openai_expert = repair_commands.add_parser(
        "invoke-openai-expert-source-edits",
        help="execute one explicitly approved OpenAI Responses expert turn",
    )
    invoke_openai_expert.add_argument("--root", type=Path, default=Path.cwd())
    invoke_openai_expert.add_argument("--request", type=Path, required=True)
    invoke_openai_expert.add_argument("--proposal", type=Path, required=True)
    invoke_openai_expert.add_argument("--debug-session", type=Path, required=True)
    invoke_openai_expert.add_argument("--source", type=Path, required=True)
    invoke_openai_expert.add_argument("--plan", type=Path, required=True)
    invoke_openai_expert.add_argument(
        "--with-openai-provider",
        action="store_true",
        required=True,
        help="explicitly opt in to one networked provider call",
    )
    invoke_openai_expert.add_argument(
        "--approve-provider-plan-digest",
        required=True,
    )
    invoke_openai_expert.add_argument("--review-note", required=True)
    invoke_openai_expert.add_argument("--envelope-output", type=Path, required=True)
    invoke_openai_expert.add_argument("--response-output", type=Path, required=True)
    invoke_openai_expert.add_argument("--edit-spec-output", type=Path, required=True)
    invoke_openai_expert.add_argument("--suggestion-report", type=Path, required=True)
    invoke_openai_expert.add_argument("--invocation-report", type=Path, required=True)
    invoke_openai_expert.add_argument(
        "--provider-execution-report", type=Path, required=True
    )
    draft_source_edits = repair_commands.add_parser(
        "draft-source-edits",
        help="qualify an external edit specification into a review-required typed plan",
    )
    draft_source_edits.add_argument("--root", type=Path, default=Path.cwd())
    draft_source_edits.add_argument("--proposal", type=Path, required=True)
    draft_source_edits.add_argument("--debug-session", type=Path, required=True)
    draft_source_edits.add_argument("--source", type=Path, required=True)
    draft_source_edits.add_argument("--edit-spec", type=Path, required=True)
    draft_source_edits.add_argument("--edit-plan-output", type=Path, required=True)
    draft_source_edits.add_argument("--planning-report", type=Path, required=True)
    apply_source_edits = repair_commands.add_parser(
        "apply-source-edits",
        help="apply an approved evidence-bound source edit plan to a candidate",
    )
    apply_source_edits.add_argument("--root", type=Path, default=Path.cwd())
    apply_source_edits.add_argument("--proposal", type=Path, required=True)
    apply_source_edits.add_argument("--debug-session", type=Path, required=True)
    apply_source_edits.add_argument("--edit-plan", type=Path, required=True)
    apply_source_edits.add_argument("--output", type=Path, required=True)
    apply_source_edits.add_argument("--application-report", type=Path, required=True)
    apply_source_edits.add_argument("--approve-proposal", required=True)
    apply_source_edits.add_argument(
        "--approve-change",
        action="append",
        required=True,
    )
    apply_source_edits.add_argument("--approve-edit-plan-digest", required=True)
    apply_source_edits.add_argument("--review-note", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "experts":
        print(
            json.dumps(
                [
                    {
                        "produces": [kind.value for kind in value.produces],
                        "purpose": value.purpose,
                        "required_tools": value.required_tools,
                        "role": value.role.value,
                    }
                    for value in EXPERT_DEFINITIONS
                ],
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "plan":
        state = OpenRTLWorkflow().create(InteractionMode(arguments.mode))
        print(json.dumps([value.value for value in state.stages]))
        return 0
    if arguments.command == "canary":
        errors = validate_fifo_canary(arguments.root.resolve())
        print(json.dumps({"errors": errors, "valid": not errors}, sort_keys=True))
        return 0 if not errors else 1
    if arguments.command == "catalog":
        catalog = LocalDesignCatalog(arguments.root.resolve())
        print(json.dumps({"package_ids": catalog.package_ids()}, sort_keys=True))
        return 0
    if arguments.command == "verified-canary":
        project_root = arguments.root.resolve()
        verified_run = load_fifo_canary_evidence(
            project_root,
            arguments.manifest,
            (*FIFO_SOURCE_REFS, FIFO_RUN_REF),
        )
        result = run_scripted_fifo(
            project_root,
            InteractionMode(arguments.mode),
            verified_run,
        )
        print(
            json.dumps(
                {
                    "covered_requirements": [
                        row.requirement_id for row in result.coverage if row.covered
                    ],
                    "evidence_id": verified_run.evidence.evidence_id,
                    "learning": result.learning is not None,
                    "package_digest": result.package.content_digest,
                    "package_id": result.package.package_id,
                    "publication_ready": result.package.publication_ready,
                    "run_id": verified_run.run.run_id,
                    "run_status": verified_run.run.status.value,
                    "trace_uri": verified_run.run.trace_uri,
                    "trust": result.package.trust.value,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "waveform":
        return _waveform_command(arguments)
    if arguments.command == "repair":
        return _repair_command(arguments)
    raise AssertionError("argparse returned an unknown command")


def _repair_command(arguments: argparse.Namespace) -> int:
    if arguments.repair_command == "prepare-expert-source-edits":
        root = arguments.root.resolve(strict=True)
        request_path = _contained_output(root, arguments.request_output)
        input_paths = {
            _contained_input(root, arguments.proposal),
            _contained_input(root, arguments.debug_session),
            _contained_input(root, arguments.source),
        }
        if request_path in input_paths:
            raise ValueError("expert source edit request output must be separate from every input")
        request = prepare_expert_source_edit_request(
            root,
            proposal_path=arguments.proposal,
            debug_session_path=arguments.debug_session,
            source_path=arguments.source,
        )
        _write_exact_repair_outputs(((request_path, request.payload()),))
        print(json.dumps(request.payload(), indent=2, sort_keys=True))
        return 0
    if arguments.repair_command == "accept-expert-source-edits":
        root = arguments.root.resolve(strict=True)
        spec_path = _contained_output(root, arguments.edit_spec_output)
        report_path = _contained_output(root, arguments.suggestion_report)
        input_paths = {
            _contained_input(root, arguments.request),
            _contained_input(root, arguments.response),
        }
        if spec_path == report_path or spec_path in input_paths or report_path in input_paths:
            raise ValueError("expert source edit outputs must be separate from every input")
        edit_spec, suggestion_report = accept_expert_source_edit_output(
            root,
            request_path=arguments.request,
            response_path=arguments.response,
        )
        _write_exact_repair_outputs(
            (
                (spec_path, edit_spec),
                (report_path, suggestion_report.payload()),
            )
        )
        print(
            json.dumps(
                {
                    "edit_spec": spec_path.relative_to(root).as_posix(),
                    "edit_spec_digest": suggestion_report.edit_spec_digest,
                    "status": "awaiting_qualification",
                    "suggestion_report": report_path.relative_to(root).as_posix(),
                    "suggestion_report_payload": suggestion_report.payload(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if arguments.repair_command == "invoke-expert-source-edits":
        return _invoke_scripted_expert_source_edits(arguments)
    if arguments.repair_command == "plan-expert-provider-invocation":
        return _plan_expert_provider_invocation(arguments)
    if arguments.repair_command == "invoke-openai-expert-source-edits":
        return _invoke_openai_expert_source_edits(arguments)
    if arguments.repair_command == "draft-source-edits":
        root = arguments.root.resolve(strict=True)
        plan_path = _contained_output(root, arguments.edit_plan_output)
        report_path = _contained_output(root, arguments.planning_report)
        input_paths = {
            _contained_input(root, arguments.proposal),
            _contained_input(root, arguments.debug_session),
            _contained_input(root, arguments.source),
            _contained_input(root, arguments.edit_spec),
        }
        if plan_path == report_path or plan_path in input_paths or report_path in input_paths:
            raise ValueError("repair planning outputs must be separate from every input")
        plan, planning_report = draft_source_edit_plan(
            root,
            proposal_path=arguments.proposal,
            debug_session_path=arguments.debug_session,
            source_path=arguments.source,
            edit_spec_path=arguments.edit_spec,
        )
        _write_exact_repair_outputs(
            (
                (plan_path, plan.payload()),
                (report_path, planning_report.payload()),
            )
        )
        print(
            json.dumps(
                {
                    "edit_plan": plan_path.relative_to(root).as_posix(),
                    "edit_plan_digest": plan.content_digest,
                    "planning_report": report_path.relative_to(root).as_posix(),
                    "planning_report_payload": planning_report.payload(),
                    "status": "awaiting_review",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if arguments.repair_command != "apply-source-edits":
        raise AssertionError("argparse returned an unknown repair command")
    root = arguments.root.resolve(strict=True)
    report_path = _contained_output(root, arguments.application_report)
    candidate_path = _contained_output(root, arguments.output)
    edit_plan = load_source_edit_plan(root, arguments.edit_plan)
    source_path = (root / edit_plan.source_path).resolve(strict=True)
    protected_paths = {
        candidate_path,
        source_path,
        (arguments.proposal if arguments.proposal.is_absolute() else root / arguments.proposal).resolve(
            strict=True
        ),
        (
            arguments.debug_session
            if arguments.debug_session.is_absolute()
            else root / arguments.debug_session
        ).resolve(strict=True),
        (
            arguments.edit_plan
            if arguments.edit_plan.is_absolute()
            else root / arguments.edit_plan
        ).resolve(strict=True),
    }
    if report_path in protected_paths:
        raise ValueError("repair application report must be separate from repair inputs and output")
    application_report = apply_reviewed_source_edits(
        root,
        proposal_path=arguments.proposal,
        debug_session_path=arguments.debug_session,
        edit_plan_path=arguments.edit_plan,
        output_path=arguments.output,
        approval=RepairApproval(
            arguments.approve_proposal,
            tuple(arguments.approve_change),
            arguments.approve_edit_plan_digest,
            arguments.review_note,
        ),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(report_path, application_report.payload())
    print(json.dumps(application_report.payload(), indent=2, sort_keys=True))
    return 0


def _invoke_scripted_expert_source_edits(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    input_paths = {
        _contained_input(root, arguments.request),
        _contained_input(root, arguments.proposal),
        _contained_input(root, arguments.debug_session),
        _contained_input(root, arguments.source),
        _contained_input(root, arguments.scripted_response),
    }
    output_paths = tuple(
        _contained_output(root, value)
        for value in (
            arguments.envelope_output,
            arguments.response_output,
            arguments.edit_spec_output,
            arguments.suggestion_report,
            arguments.invocation_report,
        )
    )
    if len(set(output_paths)) != len(output_paths) or any(
        value in input_paths for value in output_paths
    ):
        raise ValueError("expert invocation outputs must be unique and separate from inputs")
    scripted_response = _read_json_object(
        _contained_input(root, arguments.scripted_response),
        "scripted expert response",
    )
    policy = ExpertInvocationPolicy(
        arguments.runtime_binding_id,
        arguments.capability_id,
        "scripted",
        arguments.model,
        DataRetention.NOT_RETAINED,
        arguments.timeout_seconds,
        arguments.max_input_bytes,
        arguments.max_output_bytes,
        arguments.max_output_tokens,
    )
    generator = ScriptedStructuredGenerator[dict[str, Any]](
        descriptor=CapabilityDescriptor(
            capability_id=policy.capability_id,
            version="1",
            kind=CapabilityKind.STRUCTURED_GENERATION,
            features=frozenset({CapabilityFeature.STRUCTURED_OUTPUT}),
            limits={CapabilityLimit.MAX_OUTPUT_TOKENS: policy.max_output_tokens},
            data_retention=DataRetention.NOT_RETAINED,
        ),
        outcomes=(
            ScriptedStructuredGeneration(
                encoded_output=scripted_response,
                usage=GenerationUsage(),
                model=ModelMetadata(provider="scripted", model_id=policy.model),
                finish_reason=TextGenerationFinishReason.COMPLETED,
            ),
        ),
    )
    id_generator = Uuid4IdGenerator(RunId)
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=id_generator,
        cancellation=CancellationSource().token,
    )
    artifacts = asyncio.run(
        invoke_expert_source_edits(
            root,
            request_path=arguments.request,
            proposal_path=arguments.proposal,
            debug_session_path=arguments.debug_session,
            source_path=arguments.source,
            generator=generator,
            policy=policy,
            context=context,
        )
    )
    _write_exact_repair_outputs(
        (
            (output_paths[0], artifacts.envelope),
            (output_paths[1], artifacts.response),
            (output_paths[2], artifacts.edit_spec),
            (output_paths[3], artifacts.suggestion),
            (output_paths[4], artifacts.report.payload()),
        )
    )
    print(
        json.dumps(
            {
                "applies_changes": False,
                "edit_spec": output_paths[2].relative_to(root).as_posix(),
                "envelope": output_paths[0].relative_to(root).as_posix(),
                "invocation_report": output_paths[4].relative_to(root).as_posix(),
                "provider": "scripted",
                "response": output_paths[1].relative_to(root).as_posix(),
                "status": "awaiting_qualification",
                "suggestion_report": output_paths[3].relative_to(root).as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _plan_expert_provider_invocation(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    request_path = _contained_input(root, arguments.request)
    output_path = _contained_output(root, arguments.plan_output)
    if output_path == request_path:
        raise ValueError("provider invocation plan output must be separate from its request")
    plan = prepare_expert_provider_invocation_plan(
        root,
        request_path=arguments.request,
        model=arguments.model,
        credential_environment=arguments.credential_environment,
        timeout_seconds=arguments.timeout_seconds,
        max_input_bytes=arguments.max_input_bytes,
        max_output_bytes=arguments.max_output_bytes,
        max_output_tokens=arguments.max_output_tokens,
    )
    _write_exact_repair_outputs(((output_path, plan.payload()),))
    print(json.dumps(plan.payload(), indent=2, sort_keys=True))
    return 0


def _invoke_openai_expert_source_edits(arguments: argparse.Namespace) -> int:
    if not arguments.with_openai_provider:
        raise ValueError("OpenAI provider invocation requires explicit opt-in")
    root = arguments.root.resolve(strict=True)
    input_paths = {
        _contained_input(root, arguments.request),
        _contained_input(root, arguments.proposal),
        _contained_input(root, arguments.debug_session),
        _contained_input(root, arguments.source),
        _contained_input(root, arguments.plan),
    }
    output_paths = tuple(
        _contained_output(root, value)
        for value in (
            arguments.envelope_output,
            arguments.response_output,
            arguments.edit_spec_output,
            arguments.suggestion_report,
            arguments.invocation_report,
            arguments.provider_execution_report,
        )
    )
    if len(set(output_paths)) != len(output_paths) or any(
        value in input_paths for value in output_paths
    ):
        raise ValueError("provider invocation outputs must be unique and separate from inputs")
    plan = load_expert_provider_invocation_plan(root, arguments.plan)
    approval = ExpertProviderInvocationApproval(
        plan.plan_id,
        arguments.approve_provider_plan_digest,
        arguments.review_note,
    )
    approval.require_matches(plan)
    authentication = EnvironmentOpenAIAuthenticationSource(
        plan.credential_environment
    )
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=Uuid4IdGenerator(RunId),
        cancellation=CancellationSource().token,
    )
    artifacts = asyncio.run(
        invoke_approved_openai_expert_source_edits(
            root,
            request_path=arguments.request,
            proposal_path=arguments.proposal,
            debug_session_path=arguments.debug_session,
            source_path=arguments.source,
            plan_path=arguments.plan,
            approval=approval,
            context=context,
            authentication_source=authentication,
        )
    )
    invocation = artifacts.invocation
    _write_exact_repair_outputs(
        (
            (output_paths[0], invocation.envelope),
            (output_paths[1], invocation.response),
            (output_paths[2], invocation.edit_spec),
            (output_paths[3], invocation.suggestion),
            (output_paths[4], invocation.report.payload()),
            (output_paths[5], artifacts.provider_report.payload()),
        )
    )
    print(
        json.dumps(
            {
                "applies_changes": False,
                "edit_spec": output_paths[2].relative_to(root).as_posix(),
                "envelope": output_paths[0].relative_to(root).as_posix(),
                "invocation_report": output_paths[4].relative_to(root).as_posix(),
                "provider": "openai",
                "provider_execution_report": output_paths[5]
                .relative_to(root)
                .as_posix(),
                "response": output_paths[1].relative_to(root).as_posix(),
                "status": "awaiting_qualification",
                "suggestion_report": output_paths[3].relative_to(root).as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _add_waveform_selection_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("trace", type=Path)
    command.add_argument("--root", type=Path, default=Path.cwd())
    command.add_argument("--signal", action="append", default=[])
    command.add_argument("--start-fs", type=int, default=0)
    command.add_argument("--end-fs", type=int)
    command.add_argument("--max-transitions", type=int, default=200)


def _add_fifo_debug_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("trace", type=Path)
    command.add_argument("--root", type=Path, default=Path.cwd())
    command.add_argument("--start-fs", type=int, default=0)
    command.add_argument("--end-fs", type=int)
    command.add_argument("--depth", type=int)
    command.add_argument("--hierarchy", default="sync_fifo")
    command.add_argument(
        "--rtl",
        type=Path,
        default=Path("examples/fifo/rtl/sync_fifo.sv"),
    )


def _waveform_command(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    if arguments.waveform_command in ("diagnose-fifo", "propose-fifo-repair"):
        report = analyze_fifo_waveform(
            root,
            arguments.trace,
            start_fs=arguments.start_fs,
            end_fs=arguments.end_fs,
            depth=arguments.depth,
            hierarchy=arguments.hierarchy,
            rtl_path=arguments.rtl,
        )
        if arguments.waveform_command == "diagnose-fifo":
            payload = report.payload()
            if arguments.output is not None:
                output = _contained_output(root, arguments.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                _write_json(output, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report.passed else 1

        output_directory = _contained_output(root, arguments.output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        debug_path = output_directory / "debug-session.json"
        proposal_path = output_directory / "repair-proposal.json"
        focus_path = output_directory / "focus.sucl"
        proposal = propose_fifo_repairs(
            report,
            report_uri=debug_path.relative_to(root).as_posix(),
        )
        _write_json(debug_path, report.payload())
        _write_json(proposal_path, proposal.payload())
        focus_path.write_text(
            surfer_command_file(fifo_repair_focus(report)),
            encoding="utf-8",
        )
        print(json.dumps(proposal.payload(), indent=2, sort_keys=True))
        return 0

    signals = tuple(arguments.signal)
    index, inspection = inspect_vcd(
        root,
        arguments.trace,
        signals=signals,
        start_fs=arguments.start_fs,
        end_fs=arguments.end_fs,
        max_transitions=arguments.max_transitions,
    )
    payload = inspection.payload()
    if arguments.waveform_command == "inspect":
        if arguments.output is not None:
            output = _contained_output(root, arguments.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if arguments.waveform_command != "focus":
        raise AssertionError("argparse returned an unknown waveform command")
    if not signals:
        raise ValueError("waveform focus requires at least one --signal")
    if arguments.launch and arguments.surfer_executable is None:
        raise ValueError("--launch requires --surfer-executable")

    output_directory = _contained_output(root, arguments.output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    focus = index.focus(
        inspection.trace,
        signals,
        inspection.start_fs,
        inspection.end_fs,
    )
    inspection_path = output_directory / "inspection.json"
    command_path = output_directory / "focus.sucl"
    command_path.write_text(surfer_command_file(focus), encoding="utf-8")
    payload.update(
        {
            "command_file": command_path.relative_to(root).as_posix(),
            "markers_fs": focus.markers_fs,
        }
    )
    inspection_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    launched_process_id: int | None = None
    if arguments.launch:
        executable = arguments.surfer_executable.resolve(strict=True)
        if not executable.is_file():
            raise ValueError("Surfer executable must be a regular file")
        launched_process_id = asyncio.run(
            _launch_surfer(
                root,
                executable,
                (root / inspection.trace).resolve(strict=True),
                command_path,
            )
        )
    print(
        json.dumps(
            {
                "command_file": str(command_path),
                "inspection": str(inspection_path),
                "launched_process_id": launched_process_id,
                "trace": str((root / inspection.trace).resolve(strict=True)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


async def _launch_surfer(
    root: Path,
    executable: Path,
    trace: Path,
    command_file: Path,
) -> int:
    tool = build_surfer_tool(
        workspace=str(root),
        surfer_executable=str(executable),
    )
    invocation = ToolInvocation(
        invocation_id="waveform.surfer.launch",
        contract=tool.contract,
        input=CommandInput(
            arguments=("--command-file", str(command_file), str(trace)),
        ),
    )
    id_generator = Uuid4IdGenerator(RunId)
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=id_generator,
        cancellation=CancellationSource().token,
    )
    result = await tool.invoke(invocation, context)
    return result.unwrap().process_id


def _contained_output(root: Path, candidate: Path) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    lexical = selected.absolute()
    if not lexical.is_relative_to(root) or ".." in lexical.relative_to(root).parts:
        raise ValueError("output must be contained by its root")
    relative = lexical.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ValueError("output must not traverse symlinks")
    return lexical


def _contained_input(root: Path, candidate: Path) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    lexical = selected.absolute()
    if not lexical.is_relative_to(root) or ".." in lexical.relative_to(root).parts:
        raise ValueError("repair input must be contained by its root")
    current = root
    for part in lexical.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("repair input must not traverse symlinks")
    resolved = selected.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("repair input must be a regular file")
    return resolved


def _read_json_object(selected: Path, label: str) -> dict[str, Any]:
    content = selected.read_bytes()
    if len(content) > 1024 * 1024:
        raise ValueError(f"{label} exceeds the byte limit")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_exact_repair_outputs(
    outputs: tuple[tuple[Path, object], ...],
) -> None:
    encoded = tuple(
        (
            path,
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        )
        for path, payload in outputs
    )
    for path, content in encoded:
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise ValueError("repair output contains unrecognized content")
    for path, content in encoded:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)


def validate_fifo_canary(root: Path) -> tuple[str, ...]:
    if root == Path("/"):
        raise ValueError("canary root must be bounded")
    errors: list[str] = []
    for relative in _CANARY_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing:{relative}")
    specification = root / "examples/fifo/spec.md"
    if specification.is_file():
        content = specification.read_text()
        for requirement in _FIFO_REQUIREMENTS:
            if f"`{requirement}`" not in content:
                errors.append(f"missing-requirement:{requirement}")
    rtl = root / "examples/fifo/rtl/sync_fifo.sv"
    if rtl.is_file():
        content = rtl.read_text()
        for feature in ("always_ff", "assert", "write_pointer", "read_pointer"):
            if feature not in content:
                errors.append(f"missing-rtl-feature:{feature}")
    return tuple(errors)


if __name__ == "__main__":
    raise SystemExit(main())
