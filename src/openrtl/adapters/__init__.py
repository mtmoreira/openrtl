"""Infrastructure adapters for local OpenRTL execution."""

from openrtl.adapters.catalog import LocalDesignCatalog
from openrtl.adapters.fifo_debug import MAX_DEBUG_EDGES, analyze_fifo_waveform
from openrtl.adapters.fifo_repair import fifo_repair_focus, propose_fifo_repairs
from openrtl.adapters.source_edit_application import (
    apply_reviewed_source_edits,
    draft_source_edit_plan,
    load_source_edit_plan,
)
from openrtl.adapters.expert_source_edits import (
    accept_expert_source_edit_payload,
    accept_expert_source_edit_output,
    load_expert_source_edit_request,
    prepare_expert_source_edit_request,
    validate_expert_source_edit_response,
)
from openrtl.adapters.expert_invocation import (
    ExpertInvocationArtifacts,
    invoke_expert_source_edits,
)
from openrtl.adapters.provider_invocation import (
    ApprovedProviderInvocationArtifacts,
    EnvironmentOpenAIAuthenticationSource,
    RejectingArtifactResolver,
    invoke_approved_openai_expert_source_edits,
    load_expert_provider_invocation_plan,
    prepare_expert_provider_invocation_plan,
)
from openrtl.adapters.provider_qualification import qualify_provider_source_edits
from openrtl.adapters.qualified_provider_application import (
    apply_qualified_provider_source_edits,
    parse_provider_qualification_report,
)
from openrtl.adapters.candidate_promotion import (
    plan_qualified_provider_candidate_promotion,
)
from openrtl.adapters.canary import load_fifo_canary_evidence
from openrtl.adapters.agentrig import (
    OpenRTLCommandTools,
    build_command_tools,
    build_eda_mcp_binding,
    build_surfer_tool,
)
from openrtl.adapters.logs import LogEvent, LogLevel, parse_jsonl_events
from openrtl.adapters.simulation import (
    ProcessResult,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    VerilatorBackend,
)
from openrtl.adapters.waveform_workbench import (
    SignalInspection,
    WaveformInspection,
    inspect_vcd,
    surfer_command_file,
)
from openrtl.adapters.waveforms import SignalTransition, VcdIndex, WaveformFocus

__all__ = [
    "LocalDesignCatalog",
    "MAX_DEBUG_EDGES",
    "LogEvent",
    "LogLevel",
    "OpenRTLCommandTools",
    "ProcessResult",
    "SimulationRequest",
    "SimulationResult",
    "SimulationStatus",
    "SignalInspection",
    "SignalTransition",
    "VcdIndex",
    "VerilatorBackend",
    "WaveformFocus",
    "WaveformInspection",
    "build_command_tools",
    "build_eda_mcp_binding",
    "build_surfer_tool",
    "load_fifo_canary_evidence",
    "load_source_edit_plan",
    "inspect_vcd",
    "analyze_fifo_waveform",
    "apply_reviewed_source_edits",
    "accept_expert_source_edit_output",
    "accept_expert_source_edit_payload",
    "draft_source_edit_plan",
    "fifo_repair_focus",
    "parse_jsonl_events",
    "prepare_expert_source_edit_request",
    "load_expert_source_edit_request",
    "validate_expert_source_edit_response",
    "ExpertInvocationArtifacts",
    "ApprovedProviderInvocationArtifacts",
    "EnvironmentOpenAIAuthenticationSource",
    "RejectingArtifactResolver",
    "invoke_expert_source_edits",
    "invoke_approved_openai_expert_source_edits",
    "load_expert_provider_invocation_plan",
    "prepare_expert_provider_invocation_plan",
    "qualify_provider_source_edits",
    "apply_qualified_provider_source_edits",
    "parse_provider_qualification_report",
    "plan_qualified_provider_candidate_promotion",
    "propose_fifo_repairs",
    "surfer_command_file",
]
