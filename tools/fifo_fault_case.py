"""Generate and diagnose the deterministic FIFO level-update fault case."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.fifo.faults import render_fifo_trace  # noqa: E402
from openrtl.adapters import (  # noqa: E402
    analyze_fifo_waveform,
    fifo_repair_focus,
    propose_fifo_repairs,
    surfer_command_file,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/fifo-level-fault"),
    )
    parsed = parser.parse_args(arguments)
    root = parsed.root.resolve(strict=True)
    output = _contained_output(root, parsed.output_directory)
    output.mkdir(parents=True, exist_ok=True)

    trace = output / "waves.vcd"
    debug_session = output / "debug-session.json"
    repair_proposal = output / "repair-proposal.json"
    focus_commands = output / "focus.sucl"
    trace.write_text(
        render_fifo_trace(level_update_fault=True),
        encoding="utf-8",
    )
    report = analyze_fifo_waveform(
        root,
        trace,
        start_fs=20_000_000,
        end_fs=30_000_000,
        rtl_path=Path("examples/fifo/rtl/sync_fifo.sv"),
    )
    if report.passed:
        raise RuntimeError("intentional FIFO fault was not detected")
    proposal = propose_fifo_repairs(
        report,
        report_uri=debug_session.relative_to(root).as_posix(),
    )
    focus = fifo_repair_focus(report)
    _write_json(debug_session, report.payload())
    _write_json(repair_proposal, proposal.payload())
    focus_commands.write_text(surfer_command_file(focus), encoding="utf-8")

    payload: dict[str, Any] = {
        "debug_session": debug_session.relative_to(root).as_posix(),
        "finding_ids": tuple(value.finding_id for value in report.findings),
        "focus": focus_commands.relative_to(root).as_posix(),
        "focus_signals": focus.signals,
        "focus_window_fs": (focus.start_fs, focus.end_fs),
        "intended_fault": "fifo.level fails to increment after an accepted write",
        "proposal": repair_proposal.relative_to(root).as_posix(),
        "schema": "openrtl.fifo-fault-case.v1",
        "waveform": trace.relative_to(root).as_posix(),
    }
    _write_json(output / "summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _contained_output(root: Path, candidate: Path) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    resolved = selected.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("fault-case output must be contained by its root") from error
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ValueError("fault-case output must not traverse symlinks")
    return resolved


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
