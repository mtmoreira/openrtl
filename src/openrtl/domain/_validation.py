"""Small validation helpers shared by domain values."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def identifier(value: str, field: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field} must be a stable lowercase identifier")
    return normalized


def nonempty(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def digest(value: str, field: str = "digest") -> str:
    normalized = value.strip()
    if not _DIGEST.fullmatch(normalized):
        raise ValueError(f"{field} must be a sha256 digest")
    return normalized


def relative_path(value: str, field: str = "path") -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field} must be a canonical repository-relative path")
    return str(path)


def unique_identifiers(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    normalized = tuple(identifier(value, field) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} values must be unique")
    return normalized
