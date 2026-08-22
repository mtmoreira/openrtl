"""Small dependency-free VCD index used for evidence-linked review."""

from __future__ import annotations

from dataclasses import dataclass

from openrtl.domain._validation import identifier, nonempty


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
    def __init__(self, timescale_fs: int, signals: dict[str, tuple[SignalTransition, ...]]) -> None:
        if timescale_fs < 1:
            raise ValueError("timescale_fs must be positive")
        self.timescale_fs = timescale_fs
        self._signals = dict(signals)

    @classmethod
    def parse(cls, content: str) -> VcdIndex:
        symbols: dict[str, str] = {}
        transitions: dict[str, list[SignalTransition]] = {}
        scope: list[str] = []
        timestamp = 0
        timescale_fs = 1
        for raw in content.splitlines():
            line = raw.strip()
            if line.startswith("$timescale"):
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
                    symbols[symbol] = name
                    transitions.setdefault(name, [])
            elif line.startswith("#"):
                timestamp = int(line[1:]) * timescale_fs
            elif line and line[0] in "01xz" and line[1:] in symbols:
                name = symbols[line[1:]]
                transitions[name].append(SignalTransition(timestamp, line[0]))
            elif line.startswith("b"):
                parts = line.split()
                if len(parts) == 2 and parts[1] in symbols:
                    name = symbols[parts[1]]
                    transitions[name].append(SignalTransition(timestamp, parts[0][1:]))
        return cls(timescale_fs, {key: tuple(value) for key, value in transitions.items()})

    @property
    def signal_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._signals))

    def transitions(self, signal: str, start_fs: int = 0, end_fs: int | None = None) -> tuple[SignalTransition, ...]:
        if signal not in self._signals:
            raise KeyError(f"unknown waveform signal: {signal}")
        return tuple(
            value
            for value in self._signals[signal]
            if value.timestamp_fs >= start_fs and (end_fs is None or value.timestamp_fs <= end_fs)
        )

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
