"""Private atomic runtime preferences; persisted files never grant authority."""

from __future__ import annotations

import json
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import uuid
from typing import Iterator

from openrtl.domain.design_session import JsonObject, canonical, require
from openrtl.onboarding import _open_state, _private


NAMES = {"runtime.json", "runtime-selftest.json"}


@contextmanager
def writer(state: Path) -> Iterator[None]:
    """Serialize effectful selection/self-test/recovery; an interrupted process unlocks."""
    directory = _open_state(state, create=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(".runtime.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        _private(os.fstat(descriptor), directory=False)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("runtime_writer_active") from None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def load(state: Path, name: str) -> JsonObject | None:
    require(name in NAMES, "runtime_state_name_invalid")
    try:
        directory = _open_state(state, create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            _private(info, directory=False)
            require(info.st_size <= 16384, "runtime_state_too_large")
            payload = stream.read(16385)
        require(len(payload) <= 16384, "runtime_state_too_large")
        result = json.loads(payload)
        require(type(result) is dict, "runtime_state_object_required")
        assert isinstance(result, dict)
        return result
    finally:
        os.close(directory)


def save(state: Path, name: str, value: JsonObject) -> None:
    require(name in NAMES, "runtime_state_name_invalid")
    payload = canonical(value)
    require(len(payload) <= 16384, "runtime_state_too_large")
    directory = _open_state(state, create=True)
    temporary = ".runtime-" + uuid.uuid4().hex + ".tmp"
    created = False
    try:
        try:
            _private(os.stat(name, dir_fd=directory, follow_symlinks=False), directory=False)
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)
