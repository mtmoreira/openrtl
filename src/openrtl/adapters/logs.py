"""Standardized simulation and verification log events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from openrtl.domain._validation import identifier, nonempty


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LogEvent:
    timestamp_fs: int
    level: LogLevel
    component: str
    event: str
    message: str
    requirement_ids: tuple[str, ...] = ()
    fields: Mapping[str, str | int | bool] | None = None

    def __post_init__(self) -> None:
        if self.timestamp_fs < 0:
            raise ValueError("timestamp_fs must be non-negative")
        if not isinstance(self.level, LogLevel):
            raise TypeError("level must be a LogLevel")
        object.__setattr__(self, "component", identifier(self.component, "component"))
        object.__setattr__(self, "event", identifier(self.event, "event"))
        object.__setattr__(self, "message", nonempty(self.message, "message"))
        requirements = tuple(identifier(value, "requirement_id") for value in self.requirement_ids)
        if len(set(requirements)) != len(requirements):
            raise ValueError("requirement_ids must be unique")
        copied = dict(self.fields or {})
        for key, value in copied.items():
            identifier(key, "field name")
            if not isinstance(value, (str, int, bool)):
                raise TypeError("log fields must contain scalar values")
        object.__setattr__(self, "requirement_ids", requirements)
        object.__setattr__(self, "fields", MappingProxyType(copied))

    def to_json(self) -> str:
        return json.dumps(
            {
                "component": self.component,
                "event": self.event,
                "fields": dict(self.fields or {}),
                "level": self.level.value,
                "message": self.message,
                "requirement_ids": self.requirement_ids,
                "timestamp_fs": self.timestamp_fs,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def parse_jsonl_events(content: str) -> tuple[LogEvent, ...]:
    events: list[LogEvent] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError
            events.append(
                LogEvent(
                    timestamp_fs=int(value["timestamp_fs"]),
                    level=LogLevel(value["level"]),
                    component=str(value["component"]),
                    event=str(value["event"]),
                    message=str(value["message"]),
                    requirement_ids=tuple(value.get("requirement_ids", ())),
                    fields=value.get("fields", {}),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid log event at line {line_number}") from error
    return tuple(events)
