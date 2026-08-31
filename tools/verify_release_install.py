"""Verify an installed OpenRTL wheel against an extracted examples bundle."""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from typing import Sequence


EXPECTED_VERSION = "0.2.0"


def _run(arguments: Sequence[str], root: Path) -> None:
    completed = subprocess.run(arguments, cwd=root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"release example command failed: {arguments[1]}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples-root", type=Path, required=True)
    parser.add_argument("--with-verilator", action="store_true")
    arguments = parser.parse_args(argv)
    root = arguments.examples_root.resolve(strict=True)
    if version("openrtl") != EXPECTED_VERSION:
        raise RuntimeError("installed OpenRTL version is not the release candidate")
    import openrtl

    package_path = Path(openrtl.__file__).resolve(strict=True)
    if root in package_path.parents:
        raise RuntimeError("OpenRTL resolved from the examples archive, not the wheel")
    _run((sys.executable, "-m", "unittest", "examples.fifo.test_model"), root)
    _run(
        (
            sys.executable,
            "tools/fifo_fault_case.py",
            "--output-directory",
            "build/release-fifo-fault",
        ),
        root,
    )
    if arguments.with_verilator:
        _run(
            (
                sys.executable,
                "tools/fifo_repair_application_case.py",
                "--output-directory",
                "build/release-fifo-repair",
            ),
            root,
        )
    print(f"CHECKPOINT installed_openrtl version {EXPECTED_VERSION}")
    print("CHECKPOINT release_examples model_and_fault passed")
    if arguments.with_verilator:
        print("CHECKPOINT release_examples verilator_repair passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
