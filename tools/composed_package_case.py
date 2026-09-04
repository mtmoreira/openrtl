"""Explicit real RTL simulation before and after dependency-closure consumption."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any, Sequence
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openrtl.adapters import (  # noqa: E402
    DependencyClosedCatalog, PortableDesignCatalog, build_verified_package_candidate,
    load_verified_simulation_evidence, load_verified_simulation_profile,
)
from openrtl.application import PackageBundlePin  # noqa: E402
from openrtl.domain import (  # noqa: E402
    DesignPackage, InterfacePort, InterfaceRequirement, PackageDependency, Parameter,
    PackageFile, PortDirection, TrustLevel,
)
from tools.verilator_canary import VerilatorToolchain, discover_verilator_toolchain  # noqa: E402

SOURCE_PATHS = (
    'examples/fifo/rtl/sync_fifo.sv',
    'examples/skid_buffer/rtl/skid_buffer.sv',
    'examples/composed_stream/rtl/fifo_skid_stream.sv',
)
PACKAGE_IDS = ('community.sync.fifo', 'community.ready-valid.skid-buffer',
               'community.composed.fifo-skid-stream')
COUNTERS = {'accepted', 'delivered', 'backpressure', 'simultaneous', 'stalled_output',
            'resets', 'reset_with_data', 'max_occupancy'}


def read_file(path: Path, limit: int = 64 * 1024 * 1024) -> bytes:
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError('artifact must be a regular non-symlink file')
    if not 0 < path.stat().st_size <= limit:
        raise ValueError('artifact size is invalid')
    return path.read_bytes()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')


def record(root: Path, path: Path) -> dict[str, Any]:
    content = read_file(path)
    return {'path': path.relative_to(root).as_posix(), 'sha256': sha(content),
            'size_bytes': len(content)}


def validate_configuration(width: int, depth: int, seed: int) -> None:
    if type(width) is not int or not 1 <= width <= 1024:
        raise ValueError('composed width must be between 1 and 1024')
    if type(depth) is not int or not 2 <= depth <= 64:
        raise ValueError('composed simulation depth must be between 2 and 64')
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError('composed seed must be a nonnegative 32-bit integer')


def verify_run(output: Path, width: int = 8, depth: int = 4,
               seed: int = 33) -> dict[str, Any]:
    validate_configuration(width, depth, seed)
    log = read_file(output / 'run.log', 8 * 1024 * 1024)
    if b'%Warning-' in log or b'TESTS=1 PASS=1 FAIL=0 SKIP=0' not in log:
        raise ValueError('simulation summary or warning gate failed')
    xml = ET.fromstring(read_file(output / 'results.xml', 1024 * 1024))
    cases = list(xml.iter('testcase'))
    if (len(cases) != 1 or cases[0].get('name') != 'composed_stream_contract'
            or list(xml.iter('failure')) or list(xml.iter('error')) or list(xml.iter('skipped'))):
        raise ValueError('simulation testcase did not pass')
    waveform = read_file(output / 'waves.vcd')
    if b'$timescale' not in waveform or b'$enddefinitions $end' not in waveform:
        raise ValueError('simulation waveform incomplete')
    payload: Any = json.loads(read_file(output / 'coverage.json', 16384))
    if not isinstance(payload, dict) or set(payload) != {
            'schema', 'seed', 'width', 'depth', 'capacity', 'status', 'drained', 'counts'}:
        raise ValueError('coverage fields invalid')
    if (payload['schema'] != 'openrtl.composed-stream-coverage.v1' or payload['seed'] != seed
            or payload['width'] != width or payload['depth'] != depth
            or payload['capacity'] != depth + 1
            or payload['status'] != 'passed' or payload['drained'] is not True):
        raise ValueError('coverage identity invalid')
    counts = payload['counts']
    if not isinstance(counts, dict) or set(counts) != COUNTERS:
        raise ValueError('coverage counters invalid')
    if any(type(value) is not int or value <= 0 for value in counts.values()) or counts['max_occupancy'] != depth + 1:
        raise ValueError('required composed coverage missing')
    return dict(payload)


def run_bounded(command: list[str], cwd: Path, environment: dict[str, str],
                output: Path, timeout: int) -> None:
    """Bound captured output/time and terminate only this invocation's process group."""
    if not 1 <= timeout <= 600:
        raise ValueError('simulation timeout must be between 1 and 600 seconds')
    with (output / 'run.log').open('xb') as log:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        assert process.stdout is not None
        finished = False
        try:
            with selectors.DefaultSelector() as selected:
                selected.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + timeout
                total = 0
                while selected.get_map():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('composed simulation deadline exceeded')
                    for name, bound in (('waves.vcd', 64 * 1024 * 1024), ('results.xml', 1024 * 1024),
                                        ('coverage.json', 16384)):
                        file = output / name
                        if file.exists() and (file.is_symlink() or file.stat().st_size > bound):
                            raise RuntimeError('composed simulation artifact bound exceeded')
                    for key, _ in selected.select(timeout=0.1):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selected.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > 8 * 1024 * 1024:
                            raise RuntimeError('composed simulation log bound exceeded')
                        log.write(chunk)
                if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                    raise RuntimeError('composed simulation failed; inspect retained run.log')
                finished = True
        finally:
            # Also reap build/simulation descendants if a timeout or output error occurred.
            if not finished:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdout.close()


def simulate(sources: tuple[Path, ...], harness: Path, output: Path,
             toolchain: VerilatorToolchain, timeout: int, width: int = 8,
             depth: int = 4, seed: int = 33) -> dict[str, Any]:
    validate_configuration(width, depth, seed)
    if len(sources) != 3 or len(set(sources)) != 3:
        raise ValueError('exactly three distinct RTL sources required')
    for path in (*sources, harness / 'Makefile', harness / 'test_composed_stream.py', output):
        # Make variable values cannot safely represent whitespace/metacharacter paths.
        if any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-' for char in str(path)):
            raise ValueError('unsupported simulation path characters')
    source_digests = [sha(read_file(path)) for path in sources]
    harness_digests = [sha(read_file(harness / name)) for name in ('Makefile', 'test_composed_stream.py')]
    output.mkdir()
    (output / 'tmp').mkdir()
    command = [str(toolchain.make), '-f', str(harness / 'Makefile'), 'SIM=verilator',
               f'VERILATOR_BIN_DIR={toolchain.verilator.parent}',
               'VERILOG_SOURCES=' + ' '.join(str(path) for path in sources),
               f'SIM_BUILD={output / "sim_build"}', f'COCOTB_RESULTS_FILE={output / "results.xml"}',
               f'SIM_ARGS=--trace --trace-file {output / "waves.vcd"}']
    environment = {'PATH': os.pathsep.join(dict.fromkeys(str(path) for path in (
        toolchain.cocotb_config.parent, toolchain.verilator.parent, toolchain.make.parent,
        Path('/usr/bin'), Path('/bin')))), 'PYTHONPATH': str(harness),
        'TMPDIR': str(output / 'tmp'), 'COMPOSED_COVERAGE': str(output / 'coverage.json'),
        'PYTHONDONTWRITEBYTECODE': '1', 'RANDOM_SEED': str(seed),
        'COMPOSED_WIDTH': str(width), 'COMPOSED_DEPTH': str(depth),
        'COMPILE_ARGS': f'-GWIDTH={width} -GDEPTH={depth}'}
    run_bounded(command, output, environment, output, timeout)
    coverage = verify_run(output, width, depth, seed)
    if source_digests != [sha(read_file(path)) for path in sources]:
        raise ValueError('simulation changed source bytes')
    if harness_digests != [sha(read_file(harness / name)) for name in ('Makefile', 'test_composed_stream.py')]:
        raise ValueError('simulation changed trusted harness')
    report = {'schema': 'openrtl.composed-stream-run.v1', 'status': 'passed',
              'top': 'fifo_skid_stream', 'seed': seed, 'width': width, 'depth': depth,
              'capacity': depth + 1, 'command': command,
              'source_paths': [str(path) for path in sources], 'source_sha256': source_digests,
              'trusted_harness_sha256': harness_digests, 'pythonpath': str(harness),
              'environment_names': sorted(environment), 'coverage': coverage,
              'artifacts': {name: record(output, output / name) for name in
                            ('run.log', 'results.xml', 'waves.vcd', 'coverage.json')}}
    write_json(output / 'report.json', report)
    return report


def consumer_sources(workspace: Path, catalog: DependencyClosedCatalog,
                     lock_path: Path, lock_digest: str) -> tuple[Path, ...]:
    """Validate every materialized source byte before selecting the fixed RTL filelist."""
    lock = catalog.read_lock(lock_path, lock_digest)
    if {value.package_id for value in lock.packages} != set(PACKAGE_IDS):
        raise ValueError('unexpected composed package set')
    for pin in lock.packages:
        package = catalog.portable.read_package(pin.package_id, pin.version, pin.manifest_digest).package
        for file in package.files:
            source = workspace / 'packages' / package.package_id / file.path
            if 'sha256:' + sha(read_file(source)) != file.content_digest:
                raise ValueError('materialized package source digest changed')
    return tuple(workspace / 'packages' / package_id / relative
                 for package_id, relative in zip(PACKAGE_IDS, SOURCE_PATHS, strict=True))


def run_case(root: Path, output: Path, fifo_evidence: Path, skid_evidence: Path,
             toolchain: VerilatorToolchain, timeout: int, width: int = 8,
             depth: int = 4, seed: int = 33) -> dict[str, Any]:
    validate_configuration(width, depth, seed)
    root = root.resolve(strict=True)
    output = output if output.is_absolute() else root / output
    if not output.is_relative_to(root / 'build') or '..' in output.parts or output == root / 'build':
        raise ValueError('output must be a new bounded build subdirectory')
    if any(path.is_symlink() for path in (output, *output.parents)) or output.exists():
        raise ValueError('output already exists or contains a symlink; use a new output path')
    candidates = []
    for label, evidence in (('fifo', fifo_evidence), ('skid_buffer', skid_evidence)):
        profile = load_verified_simulation_profile(root, Path(f'examples/{label}/verified-profile.json'))
        verified = load_verified_simulation_evidence(root, profile, evidence)
        candidates.append(build_verified_package_candidate(root, profile, verified))
    originals = {name: read_file(root / name) for name in SOURCE_PATHS}
    output.mkdir(parents=True)
    (output / '.openrtl-composed-case-owner').write_text('openrtl-composed-package-case-v1\n')
    harness = output / 'trusted-harness'
    harness.mkdir()
    for name in ('Makefile', 'test_composed_stream.py'):
        (harness / name).write_bytes(read_file(root / 'examples/composed_stream/dv' / name))
    producer_sources = output / 'producer-rtl'
    producer_sources.mkdir()
    sources = tuple(producer_sources / Path(name).name for name in SOURCE_PATHS)
    for name, path in zip(SOURCE_PATHS, sources, strict=True):
        path.write_bytes(originals[name])
    producer = simulate(sources, harness, output / 'producer-run', toolchain, timeout,
                        width, depth, seed)
    catalog = PortableDesignCatalog(output / 'catalog')
    pins = []
    for candidate in candidates:
        receipt = catalog.store_candidate(root, candidate)
        pins.append(PackageBundlePin(receipt.package_id, receipt.version, receipt.manifest_digest))
    root_profile = output / 'composed-profile.json'
    write_json(root_profile, {'schema': 'openrtl.composed-stream-profile.v1', 'width': width, 'depth': depth,
                             'capacity': depth + 1, 'top': 'fifo_skid_stream',
                             'testcase': 'composed_stream_contract', 'seed': seed,
                             'requirements': sorted(COUNTERS), 'source_sha256': producer['source_sha256']})
    ports = tuple(InterfacePort(name, direction, width) for name, direction, width in (
        ('clk', PortDirection.INPUT, 1), ('rst_n', PortDirection.INPUT, 1),
        ('s_valid', PortDirection.INPUT, 1), ('s_ready', PortDirection.OUTPUT, 1),
        ('s_data', PortDirection.INPUT, width), ('m_valid', PortDirection.OUTPUT, 1),
        ('m_ready', PortDirection.INPUT, 1), ('m_data', PortDirection.OUTPUT, width),
        ('fifo_level', PortDirection.OUTPUT, depth.bit_length()),
        ('skid_occupied', PortDirection.OUTPUT, 1)))
    package = DesignPackage(PACKAGE_IDS[2], '1.0.0', 'composed.fifo-skid-stream', 'Apache-2.0',
                            TrustLevel.SIMULATION_VERIFIED, ports,
                            (Parameter('width', width, 1, 1024), Parameter('depth', depth, 2, 64)),
                            (PackageFile(SOURCE_PATHS[2], 'rtl', 'sha256:' + sha(originals[SOURCE_PATHS[2]])),),
                            ('ev.composed.producer',), tuple(PackageDependency(
                                value.package.package_id, value.package.version, value.package.content_digest)
                                for value in candidates))
    support = tuple(PackageFile(path.relative_to(root).as_posix(), kind, 'sha256:' + sha(read_file(path)))
                    for kind, path in (
                        ('simulation-profile', root_profile),
                        ('simulation-evidence', output / 'producer-run/report.json'),
                        ('simulation-log', output / 'producer-run/run.log'),
                        ('simulation-results', output / 'producer-run/results.xml'),
                        ('simulation-waveform', output / 'producer-run/waves.vcd')))
    receipt = catalog.store_package(root, package, support)
    pins.append(PackageBundlePin(receipt.package_id, receipt.version, receipt.manifest_digest))
    closure = DependencyClosedCatalog(output / 'catalog')
    lock = closure.resolve(package.package_id, package.version, tuple(pins))
    lock_path = output / 'closure.lock.json'
    lock_digest = closure.write_lock(lock, lock_path)
    # Delete only the exact copies created above, after proving no drift.
    for path, name in zip(sources, SOURCE_PATHS, strict=True):
        if read_file(path) != originals[name]:
            raise ValueError('producer copy changed before removal')
    for path in sources:
        path.unlink()
    producer_sources.rmdir()
    workspace = output / 'consumer'
    closure.materialize(lock_path, lock_digest, workspace,
                        (InterfaceRequirement('s_ready', PortDirection.OUTPUT, 1),),
                        (('width', width), ('depth', depth)))
    selected = consumer_sources(workspace, closure, lock_path, lock_digest)
    consumer = simulate(selected, harness, output / 'consumer-run', toolchain, timeout,
                        width, depth, seed)
    consumer_sources(workspace, closure, lock_path, lock_digest)
    if producer['coverage'] != consumer['coverage'] or producer['source_sha256'] != consumer['source_sha256']:
        raise ValueError('producer/consumer behavioral or source mismatch')
    if any(read_file(root / name) != data for name, data in originals.items()):
        raise ValueError('repository RTL changed')
    summary = {'schema': 'openrtl.composed-package-case.v1', 'status': 'passed',
               'configuration': {'width': width, 'depth': depth, 'capacity': depth + 1, 'seed': seed},
               'lock_digest': lock_digest, 'install_order': list(lock.install_order),
               'producer_rtl_copies_removed': not producer_sources.exists(),
               'consumer_source_only': all(path.is_relative_to(workspace) for path in selected),
               'coverage': consumer['coverage'], 'provider_called': False,
               'artifacts': {name: record(output, output / name) for name in (
                   'closure.lock.json', 'producer-run/report.json', 'consumer-run/report.json',
                   'consumer/openrtl-package-closure.json', 'consumer-run/waves.vcd',
                   'producer-run/waves.vcd', 'consumer-run/coverage.json',
                   'trusted-harness/Makefile', 'trusted-harness/test_composed_stream.py')}}
    write_json(output / 'evidence.json', summary)
    return summary


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--fifo-evidence', type=Path, required=True)
    parser.add_argument('--skid-evidence', type=Path, required=True)
    parser.add_argument('--verilator-executable', required=True)
    parser.add_argument('--make-executable', required=True)
    parser.add_argument('--cocotb-config-executable', required=True)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    parser.add_argument('--width', type=int, default=8)
    parser.add_argument('--depth', type=int, default=4)
    parser.add_argument('--seed', type=int, default=33)
    parsed = parser.parse_args(arguments)
    if not 1 <= parsed.timeout_seconds <= 600:
        parser.error('timeout must be between 1 and 600 seconds')
    toolchain = discover_verilator_toolchain(verilator=parsed.verilator_executable,
        make=parsed.make_executable, cocotb_config=parsed.cocotb_config_executable)
    result = run_case(parsed.root, parsed.output_directory, parsed.fifo_evidence,
                      parsed.skid_evidence, toolchain, parsed.timeout_seconds,
                      parsed.width, parsed.depth, parsed.seed)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
