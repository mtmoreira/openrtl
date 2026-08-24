"""Small dependency-free VCD index used for evidence-linked review."""

from __future__ import annotations

from dataclasses import dataclass

from openrtl.domain._validation import nonempty


_MAX_TRANSITIONS = 2_000_000


@dataclass(frozen=True)
class WaveformFocus:
    trace_uri: str
    start_fs: int
    end_fs: int
    signals: tuple[str, ...]
    markers_fs: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        nonempty(self.trace_uri, "trace_uri")
        if self.start_fs < 0 or self.end_fs < self.start_fs:
            raise ValueError("waveform focus interval is invalid")
        if not self.signals or len(set(self.signals)) != len(self.signals):
            raise ValueError("waveform focus signals must be non-empty and unique")
        if any(value < self.start_fs or value > self.end_fs for value in self.markers_fs):
            raise ValueError("waveform markers must lie inside the focus interval")


@dataclass(frozen=True)
class SignalTransition:
    timestamp_fs: int
    value: str


class VcdIndex:
    def __init__(
        self,
        timescale_fs: int,
        signals: dict[str, tuple[SignalTransition, ...]],
        end_time_fs: int,
    ) -> None:
        if timescale_fs < 1:
            raise ValueError("timescale_fs must be positive")
        if end_time_fs < 0:
            raise ValueError("end_time_fs must not be negative")
        self.timescale_fs = timescale_fs
        self._signals = dict(signals)
        self.end_time_fs = end_time_fs

    @classmethod
    def parse(cls, content: str) -> VcdIndex:
        symbols: dict[str, list[str]] = {}
        transitions: dict[str, list[SignalTransition]] = {}
        scope: list[str] = []
        timestamp = 0
        timescale_fs = 1
        transition_count = 0
        pending_timescale = False
        for raw in content.splitlines():
            line = raw.strip()
            if pending_timescale:
                if line == "$end":
                    pending_timescale = False
                elif line:
                    timescale_fs = _parse_timescale(f"$timescale {line} $end")
                continue
            if line == "$timescale":
                pending_timescale = True
            elif line.startswith("$timescale"):
                timescale_fs = _parse_timescale(line)
            elif line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    scope.append(parts[2])
            elif line.startswith("$upscope"):
                if scope:
                    scope.pop()
            elif line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:
                    symbol = parts[3]
                    name = ".".join((*scope, parts[4]))
                    symbols.setdefault(symbol, []).append(name)
                    transitions.setdefault(name, [])
            elif line.startswith("#"):
                next_timestamp = int(line[1:]) * timescale_fs
                if next_timestamp < timestamp:
                    raise ValueError("VCD timestamps must be nondecreasing")
                timestamp = next_timestamp
            elif line and line[0] in "01xXzZ" and line[1:] in symbols:
                for name in symbols[line[1:]]:
                    transitions[name].append(
                        SignalTransition(timestamp, line[0].lower())
                    )
                    transition_count += 1
            elif line.startswith(("b", "B")):
                parts = line.split()
                if len(parts) == 2 and parts[1] in symbols:
                    for name in symbols[parts[1]]:
                        transitions[name].append(
                            SignalTransition(timestamp, parts[0][1:].lower())
                        )
                        transition_count += 1
            if transition_count > _MAX_TRANSITIONS:
                raise ValueError("VCD transition count exceeds its bound")
        if pending_timescale:
            raise ValueError("VCD timescale is unterminated")
        return cls(
            timescale_fs,
            {key: tuple(value) for key, value in transitions.items()},
            timestamp,
        )

    @property
    def signal_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._signals))

    def transitions(
        self,
        signal: str,
        start_fs: int = 0,
        end_fs: int | None = None,
        limit: int | None = None,
    ) -> tuple[SignalTransition, ...]:
        if signal not in self._signals:
            raise KeyError(f"unknown waveform signal: {signal}")
        if start_fs < 0 or (end_fs is not None and end_fs < start_fs):
            raise ValueError("waveform transition interval is invalid")
        if limit is not None and (isinstance(limit, bool) or limit < 1):
            raise ValueError("waveform transition limit must be positive")
        selected = tuple(
            value
            for value in self._signals[signal]
            if value.timestamp_fs >= start_fs and (end_fs is None or value.timestamp_fs <= end_fs)
        )
        return selected if limit is None else selected[:limit]

    def value_at(self, signal: str, timestamp_fs: int) -> str | None:
        if signal not in self._signals:
            raise KeyError(f"unknown waveform signal: {signal}")
        if timestamp_fs < 0:
            raise ValueError("waveform timestamp must not be negative")
        value: str | None = None
        for transition in self._signals[signal]:
            if transition.timestamp_fs > timestamp_fs:
                break
            value = transition.value
        return value

    def focus(self, trace_uri: str, signals: tuple[str, ...], start_fs: int, end_fs: int) -> WaveformFocus:
        for signal in signals:
            if signal not in self._signals:
                raise KeyError(f"unknown waveform signal: {signal}")
        markers = tuple(
            sorted(
                {
                    item.timestamp_fs
                    for signal in signals
                    for item in self.transitions(signal, start_fs, end_fs)
                }
            )
        )
        return WaveformFocus(trace_uri, start_fs, end_fs, signals, markers)


def _parse_timescale(line: str) -> int:
    normalized = line.replace("$timescale", "").replace("$end", "").strip().replace(" ", "")
    units = {"s": 10**15, "ms": 10**12, "us": 10**9, "ns": 10**6, "ps": 10**3, "fs": 1}
    for unit in sorted(units, key=len, reverse=True):
        if normalized.endswith(unit):
            amount = int(normalized[: -len(unit)])
            return amount * units[unit]
    raise ValueError("unsupported VCD timescale")
