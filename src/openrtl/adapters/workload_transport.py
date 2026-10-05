"""Explicit, replaceable transport for design inputs to an owned guest daemon."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
from typing import Protocol

from openrtl.domain.design_session import JsonObject, content_digest, require


class WorkloadTransport(Protocol):
    @property
    def identity(self) -> JsonObject: ...

    async def stage(self, operation_id: str, inputs: Path, control: Path) -> tuple[str, str]: ...

    async def release(self, operation_id: str) -> None: ...


class LimaWorkloadTransport:
    """Copy only this operation's staged files into a selected running Lima VM.

    The Docker daemon still has to prove that it can mount and read those exact
    bytes. Selecting a Lima instance alone never establishes daemon identity.
    """

    def __init__(self, executable: Path, state_root: Path, instance: str) -> None:
        selected = executable.absolute()
        root = state_root.absolute()
        require(selected.is_file() and os.access(selected, os.X_OK) and not selected.is_symlink(),
                "lima_workload_executable_unavailable")
        require(selected.stat().st_size <= 32 * 1024 * 1024,
                "lima_workload_executable_invalid")
        require(root.is_dir() and not root.is_symlink() and
                re.fullmatch(r"managed-[a-z0-9-]{1,80}", instance) is not None,
                "lima_workload_selection_invalid")
        self.executable = selected
        self.state_root = root
        self.instance = instance
        self.identity = {"schema": "openrtl.lima-workload-transport.v1",
                         "executable": str(selected), "state_root": str(root),
                         "instance": instance,
                         "executable_sha256": "sha256:" + hashlib.sha256(selected.read_bytes()).hexdigest()}

    def _guest(self, operation_id: str) -> str:
        require(re.fullmatch(r"[a-f0-9]{32}", operation_id) is not None,
                "workload_operation_invalid")
        return "/tmp/openrtl-workloads/" + operation_id

    async def _run(self, arguments: list[str], timeout: int = 30) -> bytes:
        process = await asyncio.create_subprocess_exec(
            str(self.executable), "--tty=false", *arguments,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={"HOME": str(self.state_root), "PATH": "/usr/bin:/bin",
                 "LIMA_HOME": str(self.state_root / "lima"),
                 "XDG_CONFIG_HOME": str(self.state_root / "config"),
                 "XDG_CACHE_HOME": str(self.state_root / "cache"),
                 "LANG": "C", "LC_ALL": "C"}, cwd=self.state_root)
        async def collect() -> bytes:
            assert process.stdout is not None
            chunks: list[bytes] = []
            size = 0
            while chunk := await process.stdout.read(4096):
                size += len(chunk)
                require(size <= 8192, "lima_workload_output_limit")
                chunks.append(chunk)
            await process.wait()
            require(process.returncode == 0, "lima_workload_command_failed")
            return b"".join(chunks)
        try:
            return await asyncio.wait_for(collect(), timeout)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _shell(self, *arguments: str) -> bytes:
        # No --start, environment forwarding, shell interpolation or host-home mount.
        return await self._run(["shell", self.instance, *arguments])

    async def stage(self, operation_id: str, inputs: Path, control: Path) -> tuple[str, str]:
        guest = self._guest(operation_id)
        require(inputs.is_dir() and control.is_dir() and not inputs.is_symlink() and
                not control.is_symlink(), "workload_host_staging_invalid")
        expected: list[tuple[str, str]] = []
        for directory in (inputs, control):
            for path in sorted(directory.rglob("*")):
                require(not path.is_symlink() and (path.is_file() or path.is_dir()),
                        "workload_host_staging_invalid")
                if path.is_file():
                    require(path.stat().st_size <= 2 * 1024 * 1024,
                            "workload_file_too_large")
                    expected.append((guest + "/" + directory.name + "/" + path.relative_to(directory).as_posix(),
                                     hashlib.sha256(path.read_bytes()).hexdigest()))
        require(len(expected) <= 256, "workload_file_limit")
        await self._shell("mkdir", "-p", "-m", "0755", "/tmp/openrtl-workloads")
        # mkdir without -p refuses a collided operation and preserves its bytes.
        await self._shell("mkdir", "-m", "0700", guest)
        await self._run(["copy", "--backend=scp", "--recursive", str(inputs), str(control),
                         self.instance + ":" + guest + "/"], 60)
        # These modes affect only the copied bytes inside the selected guest.
        # UID 65534 must traverse the path and read files; host staging stays private.
        await self._shell("find", guest, "-type", "d", "-exec", "chmod", "0755", "{}", "+")
        await self._shell("find", guest, "-type", "f", "-exec", "chmod", "0644", "{}", "+")
        for path, digest in expected:
            output = await self._shell("sha256sum", path)
            require(output.decode("ascii", errors="strict").split(" ", 1)[0] == digest,
                    "guest_workload_digest_mismatch")
        return guest + "/input", guest + "/control"

    async def release(self, operation_id: str) -> None:
        guest = self._guest(operation_id)
        # This exact operation path is the only target accepted for cleanup.
        await self._shell("rm", "-rf", "--", guest)

    @property
    def identity_digest(self) -> str:
        return content_digest(self.identity)
