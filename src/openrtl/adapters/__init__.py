"""Infrastructure adapters for local OpenRTL execution."""

from openrtl.adapters.catalog import LocalDesignCatalog
from openrtl.adapters.fifo_debug import MAX_DEBUG_EDGES, analyze_fifo_waveform
from openrtl.adapters.fifo_repair import fifo_repair_focus, propose_fifo_repairs
from openrtl.adapters.fifo_repair_application import apply_reviewed_fifo_level_repair
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
    "inspect_vcd",
    "analyze_fifo_waveform",
    "apply_reviewed_fifo_level_repair",
    "fifo_repair_focus",
    "parse_jsonl_events",
    "propose_fifo_repairs",
    "surfer_command_file",
]
