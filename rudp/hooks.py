"""Optional congestion/control hooks used by the transport."""

from __future__ import annotations

from typing import Protocol


class Controller(Protocol):
    def window(self) -> int: ...
    def on_ack(self, sequence: int, rtt: float | None = None) -> None: ...
    def on_loss(self, sequence: int) -> None: ...
    def rto(self) -> float: ...


class NullController:
    """Default controller with a fixed sending window and timeout."""

    def __init__(self, window_size: int = 32, timeout: float = 0.25) -> None:
        self._window = max(1, int(window_size))
        self._timeout = max(0.001, float(timeout))

    def window(self) -> int:
        return self._window

    def on_ack(self, sequence: int, rtt: float | None = None) -> None:
        del sequence, rtt

    def on_loss(self, sequence: int) -> None:
        del sequence

    def rto(self) -> float:
        return self._timeout
