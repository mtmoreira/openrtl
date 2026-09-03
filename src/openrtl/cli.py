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
    DependencyClosedCatalog,
    LocalDesignCatalog,
    PortableDesignCatalog,
    EnvironmentOpenAIAuthenticationSource,
    accept_expert_source_edit_output,
    analyze_fifo_waveform,
    analyze_skid_buffer_waveform,
    apply_qualified_provider_source_edits,
    apply_reviewed_source_edits,
    build_surfer_tool,
    build_verified_package_candidate,
    draft_source_edit_plan,
    fifo_repair_focus,
    inspect_vcd,
    invoke_expert_source_edits,
    invoke_approved_openai_expert_source_edits,
    load_expert_provider_invocation_plan,
    load_fifo_canary_evidence,
    load_verified_simulation_evidence,
    load_verified_simulation_profile,
    load_source_edit_plan,
    plan_qualified_provider_candidate_promotion,
    promote_qualified_provider_candidate,
    prepare_expert_source_edit_request,
    prepare_expert_provider_invocation_plan,
    qualify_provider_source_edits,
    propose_fifo_repairs,
    propose_skid_buffer_repairs,
    skid_buffer_repair_focus,
    surfer_command_file,
)
from openrtl.application import (
    EXPERT_DEFINITIONS,
    ExpertInvocationPolicy,
    ExpertProviderInvocationApproval,
    FIFO_RUN_REF,
    FIFO_SOURCE_REFS,
    OpenRTLWorkflow,
    PackageBundlePin,
    CandidatePromotionApproval,
    QualifiedProviderRepairApproval,
    RepairApproval,
    run_scripted_fifo,
)
from openrtl.domain import InterfaceRequirement, InteractionMode, PortDirection


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
    verified_package = subcommands.add_parser(
        "verified-package",
        help="build a package candidate from an explicit simulation profile",
    )
    verified_package.add_argument("--root", type=Path, default=Path.cwd())
    verified_package.add_argument("--profile", type=Path, required=True)
    verified_package.add_argument("--manifest", type=Path, required=True)
    verified_package.add_argument("--catalog-root", type=Path)
    portable_package = subcommands.add_parser(
        "portable-package",
        help="store a self-contained verified package bundle",
    )
    portable_package.add_argument("--root", type=Path, default=Path.cwd())
    portable_package.add_argument("--profile", type=Path, required=True)
    portable_package.add_argument("--manifest", type=Path, required=True)
    portable_package.add_argument("--catalog-root", type=Path, required=True)
    materialize_package = subcommands.add_parser(
        "materialize-package",
        help="verify, compatibility-check, and materialize a portable package",
    )
    materialize_package.add_argument("--catalog-root", type=Path, required=True)
    materialize_package.add_argument("--package-id", required=True)
    materialize_package.add_argument("--version", required=True)
    materialize_package.add_argument("--expected-manifest-digest", required=True)
    materialize_package.add_argument("--destination", type=Path, required=True)
    materialize_package.add_argument("--require-port", action="append", required=True)
    materialize_package.add_argument("--parameter", action="append", default=[])
    lock_closure = subcommands.add_parser(
        "lock-package-closure",
        help="resolve an exact digest-pinned portable package dependency closure",
    )
    lock_closure.add_argument("--catalog-root", type=Path, required=True)
    lock_closure.add_argument("--root-package-id", required=True)
    lock_closure.add_argument("--root-version", required=True)
    lock_closure.add_argument("--bundle-pin", action="append", required=True)
    lock_closure.add_argument("--output", type=Path, required=True)
    materialize_closure = subcommands.add_parser(
        "materialize-package-closure",
        help="verify a closure lock and atomically materialize every package",
    )
    materialize_closure.add_argument("--catalog-root", type=Path, required=True)
    materialize_closure.add_argument("--lock", type=Path, required=True)
    materialize_closure.add_argument("--expected-lock-digest", required=True)
    materialize_closure.add_argument("--destination", type=Path, required=True)
    materialize_closure.add_argument("--require-port", action="append", required=True)
    materialize_closure.add_argument("--parameter", action="append", default=[])
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
    diagnose_skid = waveform_commands.add_parser(
        "diagnose-skid-buffer",
        help="explain skid-buffer clock-edge behavior and flag contract violations",
    )
    _add_skid_buffer_debug_arguments(diagnose_skid)
    diagnose_skid.add_argument("--output", type=Path)
    propose_skid = waveform_commands.add_parser(
        "propose-skid-buffer-repair",
        help="derive a non-applying repair proposal from skid-buffer findings",
    )
    _add_skid_buffer_debug_arguments(propose_skid)
    propose_skid.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/skid-buffer-repair-proposal"),
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
    qualify_provider_edits = repair_commands.add_parser(
        "qualify-provider-source-edits",
        help="bind exact provider lifecycle evidence to a review-required edit plan",
    )
    qualify_provider_edits.add_argument("--root", type=Path, default=Path.cwd())
    qualify_provider_edits.add_argument("--proposal", type=Path, required=True)
    qualify_provider_edits.add_argument("--debug-session", type=Path, required=True)
    qualify_provider_edits.add_argument("--source", type=Path, required=True)
    qualify_provider_edits.add_argument("--provider-plan", type=Path, required=True)
    qualify_provider_edits.add_argument(
        "--provider-execution-report", type=Path, required=True
    )
    qualify_provider_edits.add_argument("--invocation-report", type=Path, required=True)
    qualify_provider_edits.add_argument("--suggestion-report", type=Path, required=True)
    qualify_provider_edits.add_argument("--edit-spec", type=Path, required=True)
    qualify_provider_edits.add_argument("--edit-plan-output", type=Path, required=True)
    qualify_provider_edits.add_argument("--planning-report", type=Path, required=True)
    qualify_provider_edits.add_argument(
        "--qualification-report", type=Path, required=True
    )
    apply_qualified_provider_edits = repair_commands.add_parser(
        "apply-qualified-provider-source-edits",
        help="apply an explicitly approved provider qualification to a candidate",
    )
    apply_qualified_provider_edits.add_argument("--root", type=Path, default=Path.cwd())
    apply_qualified_provider_edits.add_argument("--proposal", type=Path, required=True)
    apply_qualified_provider_edits.add_argument(
        "--debug-session", type=Path, required=True
    )
    apply_qualified_provider_edits.add_argument("--edit-plan", type=Path, required=True)
    apply_qualified_provider_edits.add_argument(
        "--planning-report", type=Path, required=True
    )
    apply_qualified_provider_edits.add_argument(
        "--qualification-report", type=Path, required=True
    )
    apply_qualified_provider_edits.add_argument("--output", type=Path, required=True)
    apply_qualified_provider_edits.add_argument(
        "--application-report", type=Path, required=True
    )
    apply_qualified_provider_edits.add_argument(
        "--qualified-application-report", type=Path, required=True
    )
    apply_qualified_provider_edits.add_argument("--approve-qualification", required=True)
    apply_qualified_provider_edits.add_argument(
        "--approve-qualification-digest", required=True
    )
    apply_qualified_provider_edits.add_argument("--approve-proposal", required=True)
    apply_qualified_provider_edits.add_argument(
        "--approve-change", action="append", required=True
    )
    apply_qualified_provider_edits.add_argument(
        "--approve-edit-plan-digest", required=True
    )
    apply_qualified_provider_edits.add_argument("--review-note", required=True)
    plan_candidate_promotion = repair_commands.add_parser(
        "plan-qualified-provider-candidate-promotion",
        help="bind validated candidate evidence into a non-applying promotion plan",
    )
    plan_candidate_promotion.add_argument("--root", type=Path, default=Path.cwd())
    plan_candidate_promotion.add_argument(
        "--qualification-report", type=Path, required=True
    )
    plan_candidate_promotion.add_argument(
        "--application-report", type=Path, required=True
    )
    plan_candidate_promotion.add_argument(
        "--qualified-application-report", type=Path, required=True
    )
    plan_candidate_promotion.add_argument("--candidate", type=Path, required=True)
    plan_candidate_promotion.add_argument("--target-source", type=Path, required=True)
    plan_candidate_promotion.add_argument("--comparison", type=Path, required=True)
    plan_candidate_promotion.add_argument("--evidence", type=Path, required=True)
    plan_candidate_promotion.add_argument(
        "--promotion-plan-output", type=Path, required=True
    )
    promote_candidate = repair_commands.add_parser(
        "promote-qualified-provider-candidate",
        help="replace one exact target with an independently approved candidate",
    )
    promote_candidate.add_argument("--root", type=Path, default=Path.cwd())
    promote_candidate.add_argument("--promotion-plan", type=Path, required=True)
    promote_candidate.add_argument("--candidate", type=Path, required=True)
    promote_candidate.add_argument("--target-source", type=Path, required=True)
    promote_candidate.add_argument(
        "--promotion-receipt-output", type=Path, required=True
    )
    promote_candidate.add_argument("--approve-promotion-plan-id", required=True)
    promote_candidate.add_argument("--approve-promotion-plan-digest", required=True)
    promote_candidate.add_argument("--approve-target-path", required=True)
    promote_candidate.add_argument("--approve-target-digest", required=True)
    promote_candidate.add_argument("--approve-candidate-digest", required=True)
    promote_candidate.add_argument("--signoff-note", required=True)
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
    if arguments.command == "verified-package":
        project_root = arguments.root.resolve()
        profile = load_verified_simulation_profile(project_root, arguments.profile)
        verified_run = load_verified_simulation_evidence(
            project_root,
            profile,
            arguments.manifest,
        )
        selected_catalog = (
            LocalDesignCatalog(arguments.catalog_root.resolve())
            if arguments.catalog_root is not None
            else None
        )
        candidate = build_verified_package_candidate(
            project_root,
            profile,
            verified_run,
            selected_catalog,
        )
        print(
            json.dumps(
                {
                    "catalog_manifest": candidate.catalog_manifest,
                    "covered_requirements": [
                        value.requirement_id for value in candidate.coverage if value.covered
                    ],
                    "design_id": candidate.package.design_id,
                    "evidence_id": candidate.verified_run.evidence.evidence_id,
                    "package_digest": candidate.package.content_digest,
                    "package_id": candidate.package.package_id,
                    "profile_digest": candidate.profile.profile_digest,
                    "profile_id": candidate.profile.profile_id,
                    "publication_ready": candidate.package.publication_ready,
                    "run_id": candidate.verified_run.run.run_id,
                    "trust": candidate.package.trust.value,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "portable-package":
        project_root = arguments.root.resolve()
        profile = load_verified_simulation_profile(project_root, arguments.profile)
        verified_run = load_verified_simulation_evidence(project_root, profile, arguments.manifest)
        candidate = build_verified_package_candidate(project_root, profile, verified_run)
        receipt = PortableDesignCatalog(arguments.catalog_root.resolve()).store_candidate(
            project_root,
            candidate,
        )
        print(
            json.dumps(
                {
                    "manifest_digest": receipt.manifest_digest,
                    "manifest_uri": receipt.manifest_uri,
                    "package_digest": receipt.package_digest,
                    "package_id": receipt.package_id,
                    "version": receipt.version,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "materialize-package":
        required_ports = tuple(_parse_required_port(value) for value in arguments.require_port)
        parameter_values = tuple(_parse_parameter_value(value) for value in arguments.parameter)
        report = PortableDesignCatalog(arguments.catalog_root.resolve()).materialize(
            arguments.package_id,
            arguments.version,
            arguments.expected_manifest_digest,
            arguments.destination.resolve(),
            required_ports,
            parameter_values,
        )
        print(
            json.dumps(
                {
                    "bundle_manifest_digest": report.bundle_manifest_digest,
                    "destination": report.destination,
                    "materialized_files": report.materialized_files,
                    "package_digest": report.package_digest,
                    "package_id": report.package_id,
                    "receipt": report.receipt_uri,
                    "version": report.version,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "lock-package-closure":
        closure_catalog = DependencyClosedCatalog(arguments.catalog_root.resolve())
        lock = closure_catalog.resolve(
            arguments.root_package_id,
            arguments.root_version,
            tuple(_parse_bundle_pin(value) for value in arguments.bundle_pin),
        )
        lock_digest = closure_catalog.write_lock(lock, arguments.output.resolve())
        print(
            json.dumps(
                {
                    "install_order": lock.install_order,
                    "lock": arguments.output.resolve().as_posix(),
                    "lock_digest": lock_digest,
                    "packages": [value.package_id for value in lock.packages],
                    "root_package_id": lock.root_package_id,
                    "root_version": lock.root_version,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "materialize-package-closure":
        closure_report = DependencyClosedCatalog(arguments.catalog_root.resolve()).materialize(
            arguments.lock.resolve(),
            arguments.expected_lock_digest,
            arguments.destination.resolve(),
            tuple(_parse_required_port(value) for value in arguments.require_port),
            tuple(_parse_parameter_value(value) for value in arguments.parameter),
        )
        print(
            json.dumps(
                {
                    "destination": closure_report.destination,
                    "install_order": closure_report.install_order,
                    "lock_digest": closure_report.lock_digest,
                    "package_receipts": closure_report.materialization_receipts,
                    "receipt": closure_report.receipt_uri,
                    "root_package_id": closure_report.root_package_id,
                    "root_version": closure_report.root_version,
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
    if arguments.repair_command == "qualify-provider-source-edits":
        return _qualify_provider_source_edits(arguments)
    if arguments.repair_command == "apply-qualified-provider-source-edits":
        return _apply_qualified_provider_source_edits(arguments)
    if arguments.repair_command == "plan-qualified-provider-candidate-promotion":
        return _plan_qualified_provider_candidate_promotion(arguments)
    if arguments.repair_command == "promote-qualified-provider-candidate":
        return _promote_qualified_provider_candidate(arguments)
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


def _qualify_provider_source_edits(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    input_paths = {
        _contained_input(root, arguments.proposal),
        _contained_input(root, arguments.debug_session),
        _contained_input(root, arguments.source),
        _contained_input(root, arguments.provider_plan),
        _contained_input(root, arguments.provider_execution_report),
        _contained_input(root, arguments.invocation_report),
        _contained_input(root, arguments.suggestion_report),
        _contained_input(root, arguments.edit_spec),
    }
    output_paths = tuple(
        _contained_output(root, value)
        for value in (
            arguments.edit_plan_output,
            arguments.planning_report,
            arguments.qualification_report,
        )
    )
    if len(set(output_paths)) != len(output_paths) or any(
        value in input_paths for value in output_paths
    ):
        raise ValueError(
            "provider qualification outputs must be unique and separate from inputs"
        )
    edit_plan, planning, qualification = qualify_provider_source_edits(
        root,
        proposal_path=arguments.proposal,
        debug_session_path=arguments.debug_session,
        source_path=arguments.source,
        provider_plan_path=arguments.provider_plan,
        provider_execution_report_path=arguments.provider_execution_report,
        invocation_report_path=arguments.invocation_report,
        suggestion_report_path=arguments.suggestion_report,
        edit_spec_path=arguments.edit_spec,
    )
    _write_exact_repair_outputs(
        (
            (output_paths[0], edit_plan.payload()),
            (output_paths[1], planning.payload()),
            (output_paths[2], qualification.payload()),
        )
    )
    print(
        json.dumps(
            {
                "applies_changes": False,
                "edit_plan": output_paths[0].relative_to(root).as_posix(),
                "edit_plan_digest": edit_plan.content_digest,
                "planning_report": output_paths[1].relative_to(root).as_posix(),
                "provider_output_trusted": False,
                "qualification_digest": qualification.content_digest,
                "qualification_report": output_paths[2].relative_to(root).as_posix(),
                "status": "awaiting_review",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _apply_qualified_provider_source_edits(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    input_paths = {
        _contained_input(root, arguments.proposal),
        _contained_input(root, arguments.debug_session),
        _contained_input(root, arguments.edit_plan),
        _contained_input(root, arguments.planning_report),
        _contained_input(root, arguments.qualification_report),
    }
    edit_plan = load_source_edit_plan(root, arguments.edit_plan)
    source_path = _contained_input(root, Path(edit_plan.source_path))
    input_paths.add(source_path)
    output_paths = tuple(
        _contained_output(root, value)
        for value in (
            arguments.output,
            arguments.application_report,
            arguments.qualified_application_report,
        )
    )
    if len(set(output_paths)) != len(output_paths) or any(
        value in input_paths for value in output_paths
    ):
        raise ValueError(
            "qualified provider application outputs must be unique and separate from inputs"
        )
    approval = QualifiedProviderRepairApproval(
        arguments.approve_qualification,
        arguments.approve_qualification_digest,
        arguments.approve_proposal,
        tuple(arguments.approve_change),
        arguments.approve_edit_plan_digest,
        arguments.review_note,
    )
    application, qualified = apply_qualified_provider_source_edits(
        root,
        proposal_path=arguments.proposal,
        debug_session_path=arguments.debug_session,
        edit_plan_path=arguments.edit_plan,
        planning_report_path=arguments.planning_report,
        qualification_report_path=arguments.qualification_report,
        output_path=arguments.output,
        approval=approval,
    )
    _write_exact_repair_outputs(
        (
            (output_paths[1], application.payload()),
            (output_paths[2], qualified.payload()),
        )
    )
    print(
        json.dumps(
            {
                "application_report": output_paths[1].relative_to(root).as_posix(),
                "candidate": output_paths[0].relative_to(root).as_posix(),
                "qualification_digest": qualified.qualification_digest,
                "qualified_application_id": qualified.qualified_application_id,
                "qualified_application_report": output_paths[2]
                .relative_to(root)
                .as_posix(),
                "status": "applied_to_candidate",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _plan_qualified_provider_candidate_promotion(
    arguments: argparse.Namespace,
) -> int:
    root = arguments.root.resolve(strict=True)
    input_paths = {
        _contained_input(root, arguments.qualification_report),
        _contained_input(root, arguments.application_report),
        _contained_input(root, arguments.qualified_application_report),
        _contained_input(root, arguments.candidate),
        _contained_input(root, arguments.target_source),
        _contained_input(root, arguments.comparison),
        _contained_input(root, arguments.evidence),
    }
    output_path = _contained_output(root, arguments.promotion_plan_output)
    if output_path in input_paths:
        raise ValueError("candidate promotion plan output must be separate from every input")
    plan = plan_qualified_provider_candidate_promotion(
        root,
        qualification_report_path=arguments.qualification_report,
        application_report_path=arguments.application_report,
        qualified_application_report_path=arguments.qualified_application_report,
        candidate_path=arguments.candidate,
        target_path=arguments.target_source,
        comparison_path=arguments.comparison,
        evidence_path=arguments.evidence,
    )
    _write_exact_repair_outputs(((output_path, plan.payload()),))
    print(
        json.dumps(
            {
                "applies_changes": False,
                "candidate": plan.candidate_path,
                "promotion_plan": output_path.relative_to(root).as_posix(),
                "promotion_plan_digest": plan.content_digest,
                "promotion_plan_id": plan.promotion_plan_id,
                "status": "awaiting_promotion_approval",
                "target": plan.target_path,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _promote_qualified_provider_candidate(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    plan_path = _contained_input(root, arguments.promotion_plan)
    candidate_path = _contained_input(root, arguments.candidate)
    target_path = _contained_input(root, arguments.target_source)
    receipt_path = _contained_output(root, arguments.promotion_receipt_output)
    if receipt_path in {plan_path, candidate_path, target_path}:
        raise ValueError("promotion receipt must be separate from every input")
    approval = CandidatePromotionApproval(
        arguments.approve_promotion_plan_id,
        arguments.approve_promotion_plan_digest,
        arguments.approve_target_path,
        arguments.approve_target_digest,
        arguments.approve_candidate_digest,
        arguments.signoff_note,
    )
    receipt = promote_qualified_provider_candidate(
        root,
        promotion_plan_path=arguments.promotion_plan,
        candidate_path=arguments.candidate,
        target_path=arguments.target_source,
        approval=approval,
    )
    _write_exact_repair_outputs(((receipt_path, receipt.payload()),))
    print(
        json.dumps(
            {
                "applies_changes": True,
                "promotion_id": receipt.promotion_id,
                "promotion_receipt": receipt_path.relative_to(root).as_posix(),
                "status": "promoted_to_production",
                "target": receipt.target_path,
                "target_digest_after": receipt.target_digest_after,
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


def _add_skid_buffer_debug_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("trace", type=Path)
    command.add_argument("--root", type=Path, default=Path.cwd())
    command.add_argument("--start-fs", type=int, default=0)
    command.add_argument("--end-fs", type=int)
    command.add_argument("--hierarchy", default="skid_buffer")
    command.add_argument(
        "--rtl",
        type=Path,
        default=Path("examples/skid_buffer/rtl/skid_buffer.sv"),
    )


def _waveform_command(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    if arguments.waveform_command in (
        "diagnose-skid-buffer",
        "propose-skid-buffer-repair",
    ):
        report = analyze_skid_buffer_waveform(
            root,
            arguments.trace,
            start_fs=arguments.start_fs,
            end_fs=arguments.end_fs,
            hierarchy=arguments.hierarchy,
            rtl_path=arguments.rtl,
        )
        if arguments.waveform_command == "diagnose-skid-buffer":
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
        proposal = propose_skid_buffer_repairs(
            report,
            report_uri=debug_path.relative_to(root).as_posix(),
        )
        _write_json(debug_path, report.payload())
        _write_json(proposal_path, proposal.payload())
        focus_path.write_text(
            surfer_command_file(skid_buffer_repair_focus(report)),
            encoding="utf-8",
        )
        print(json.dumps(proposal.payload(), indent=2, sort_keys=True))
        return 0

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


def _parse_required_port(value: str) -> InterfaceRequirement:
    fields = value.split(":")
    if len(fields) != 3:
        raise ValueError("required port must use NAME:DIRECTION:WIDTH")
    try:
        direction = PortDirection(fields[1])
        width = int(fields[2])
    except ValueError as error:
        raise ValueError("required port direction or width is invalid") from error
    return InterfaceRequirement(fields[0], direction, width)


def _parse_bundle_pin(value: str) -> PackageBundlePin:
    identity, separator, manifest_digest = value.partition("=")
    package_id, version_separator, version = identity.rpartition("@")
    if not separator or not version_separator or not package_id or not version or not manifest_digest:
        raise ValueError("bundle pin must use PACKAGE_ID@VERSION=MANIFEST_DIGEST")
    return PackageBundlePin(package_id, version, manifest_digest)


def _parse_parameter_value(value: str) -> tuple[str, int | bool | str]:
    name, separator, encoded = value.partition("=")
    if not separator or not name or not encoded:
        raise ValueError("parameter must use NAME=VALUE")
    if encoded == "true":
        parsed: int | bool | str = True
    elif encoded == "false":
        parsed = False
    else:
        try:
            parsed = int(encoded)
        except ValueError:
            parsed = encoded
    return name, parsed


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
