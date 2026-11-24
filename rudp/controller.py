"""Congestion controller interface and implementations for RUDP.

This module defines the extensible CongestionController interface and provides
concrete implementations:
  - NullController: Fixed-window controller (baseline/backward compatibility).
  - RenoController: RFC 5681 TCP Reno congestion control (Slow Start, Congestion
    Avoidance, Fast Retransmit, and Fast Recovery) with structured cwnd logging.

Future weeks extend this foundation:
  - Week 2: Dynamic RTO calculation and backoff (RFC 6298).
  - Week 3: End-to-end Reno evaluation and tuning.
  - Week 4: CUBIC congestion controller variant (RFC 8312).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
import time
from typing import Any, Callable, Protocol, runtime_checkable

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, MAX_RTO, MIN_RTO
from .eventlog import EventLogger


class CongestionState(str, Enum):
    """Congestion control state machine states (RFC 5681)."""

    SLOW_START = "slow_start"
    CONGESTION_AVOIDANCE = "congestion_avoidance"
    FAST_RECOVERY = "fast_recovery"


@runtime_checkable
class Controller(Protocol):
    """Minimal controller protocol preserved for backward compatibility."""

    def window(self) -> int: ...
    def on_ack(self, sequence: int, rtt: float | None = None) -> None: ...
    def on_loss(self, sequence: int) -> None: ...
    def rto(self) -> float: ...


@runtime_checkable
class CongestionController(Controller, Protocol):
    """Full protocol interface for RUDP congestion control algorithms."""

    @property
    def cwnd(self) -> float:
        """Current congestion window size in packets/segments."""
        ...

    @property
    def ssthresh(self) -> float:
        """Slow start threshold in packets/segments."""
        ...

    @property
    def state(self) -> CongestionState:
        """Current congestion control state."""
        ...

    def on_packet_sent(self, sequence: int, size: int = 1) -> None:
        """Hook called when a packet is transmitted onto the network."""
        ...

    def on_duplicate_ack(self, ack_number: int) -> bool:
        """Hook called when a duplicate ACK is received.

        Returns True if Fast Retransmit was triggered (typically at 3 duplicate ACKs).
        """
        ...

    def on_rto_timeout(self) -> None:
        """Hook called when a retransmission timeout (RTO) expires."""
        ...

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of controller metrics and state."""
        ...


class BaseController(ABC):
    """Abstract base class providing common controller functionality."""

    def __init__(self, logger: EventLogger | None = None) -> None:
        self._logger = logger
        self._change_observers: list[Callable[[dict[str, Any]], None]] = []
        self._srtt: float | None = None
        self._rttvar: float | None = None

    def add_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback for window/state change notifications."""
        if callback not in self._change_observers:
            self._change_observers.append(callback)

    def remove_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Unregister a window/state callback."""
        if callback in self._change_observers:
            self._change_observers.remove(callback)

    def _notify_observers(self, event_data: dict[str, Any]) -> None:
        for observer in list(self._change_observers):
            try:
                observer(event_data)
            except Exception:
                pass

    def _update_rto(self, rtt: float | None, minimum: float, maximum: float) -> None:
        if rtt is None or rtt < 0:
            return
        if self._srtt is None:
            self._srtt = rtt
            self._rttvar = rtt / 2
        else:
            assert self._rttvar is not None
            self._rttvar = 0.75 * self._rttvar + 0.25 * abs(self._srtt - rtt)
            self._srtt = 0.875 * self._srtt + 0.125 * rtt
        self._timeout = min(maximum, max(minimum, self._srtt + max(0.001, 4 * self._rttvar)))

    def observe_rtt(self, rtt: float) -> None:
        """Seed the RTO estimator with a sample that must not move cwnd (handshake RTT)."""
        self._update_rto(rtt, MIN_RTO, MAX_RTO)

    @abstractmethod
    def window(self) -> int:
        """Return the current sending window in packets."""
        raise NotImplementedError

    @abstractmethod
    def rto(self) -> float:
        """Return the current retransmission timeout in seconds."""
        raise NotImplementedError

    @abstractmethod
    def on_ack(self, sequence: int, rtt: float | None = None) -> None:
        """Process an acknowledgement."""
        raise NotImplementedError

    @abstractmethod
    def on_loss(self, sequence: int) -> None:
        """Process a retransmission-timeout loss event."""
        raise NotImplementedError

    def on_fast_retransmit(self, sequence: int) -> None:
        """Observe a fast retransmission without applying RTO backoff.

        Reno's congestion response is already performed when the third
        duplicate ACK enters Fast Recovery. Other controllers may override
        this hook if they need separate fast-loss accounting.
        """
        del sequence


    def on_recovery_complete(self) -> None:
        """Called by the sender when the cumulative ACK passes a loss event."""


class NullController(BaseController):
    """Static window controller without dynamic congestion adjustments.

    Maintains a fixed window and static timeout for baseline benchmarking
    and test compatibility.
    """

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW,
        timeout: float = DEFAULT_RTO,
        logger: EventLogger | None = None,
    ) -> None:
        super().__init__(logger=logger)
        self._window = max(1, int(window_size))
        self._timeout = max(0.001, float(timeout))
        self._state = CongestionState.CONGESTION_AVOIDANCE

    @property
    def cwnd(self) -> float:
        return float(self._window)

    @property
    def ssthresh(self) -> float:
        return float(self._window)

    @property
    def state(self) -> CongestionState:
        return self._state

    def window(self) -> int:
        return self._window

    def rto(self) -> float:
        return self._timeout

    def on_packet_sent(self, sequence: int, size: int = 1) -> None:
        del sequence, size

    def on_ack(self, sequence: int, rtt: float | None = None) -> None:
        del sequence
        self._update_rto(rtt, MIN_RTO, MAX_RTO)

    def on_duplicate_ack(self, ack_number: int) -> bool:
        del ack_number
        return False

    def on_loss(self, sequence: int) -> None:
        del sequence

    def on_fast_retransmit(self, sequence: int) -> None:
        del sequence

    def on_rto_timeout(self) -> None:
        self._timeout = min(MAX_RTO, self._timeout * 2)

    def stats(self) -> dict[str, Any]:
        return {
            "type": "NullController",
            "cwnd": self._window,
            "ssthresh": self._window,
            "state": self._state.value,
            "rto": self._timeout,
        }


class RenoController(BaseController):
    """TCP Reno Congestion Controller (RFC 5681).

    Implements:
      - Slow Start: cwnd starts at initial_cwnd, increases by 1.0 for each
        valid ACK (exponential window growth per RTT).
      - Congestion Avoidance: when cwnd >= ssthresh, increases by 1.0 / cwnd
        for each valid ACK (additive increase, +1 packet per RTT).
      - Fast Retransmit & Fast Recovery: on 3 duplicate ACKs:
          ssthresh = max(2, cwnd / 2)
          cwnd = ssthresh + 3
        While in Fast Recovery, each additional duplicate ACK inflates cwnd by 1.
        Upon arrival of an ACK acknowledging new data, cwnd deflates to ssthresh
        and state returns to Congestion Avoidance.
      - Retransmission Timeout (RTO):
          ssthresh = max(2, cwnd / 2)
          cwnd = min_cwnd (1.0)
          state = Slow Start
    """

    DUP_ACK_THRESHOLD = 3

    def __init__(
        self,
        initial_cwnd: float = 1.0,
        initial_ssthresh: float = 64.0,
        min_cwnd: float = 1.0,
        max_cwnd: float = 65535.0,
        timeout: float = DEFAULT_RTO,
        logger: EventLogger | None = None,
    ) -> None:
        super().__init__(logger=logger)
        self._initial_cwnd = max(1.0, float(initial_cwnd))
        self._min_cwnd = max(1.0, float(min_cwnd))
        self._max_cwnd = max(self._min_cwnd, float(max_cwnd))
        self._cwnd: float = self._initial_cwnd
        self._ssthresh: float = max(2.0, float(initial_ssthresh))
        self._state: CongestionState = (
            CongestionState.SLOW_START
            if self._cwnd < self._ssthresh
            else CongestionState.CONGESTION_AVOIDANCE
        )
        self._timeout: float = max(0.001, float(timeout))
        self._dup_acks: int = 0
        self._last_ack_number: int | None = None
        self._fast_recovery_seq: int | None = None
        # True when the sender (SACK scoreboard) owns recovery exit via
        # on_recovery_complete(); False for the classic dup-ACK path, which
        # exits on the first ACK of new data.
        self._managed_recovery: bool = False
        self._history: list[dict[str, Any]] = []

        self._log_state_change("init")

    @property
    def cwnd(self) -> float:
        """Current congestion window in packets."""
        return self._cwnd

    @property
    def ssthresh(self) -> float:
        """Current slow start threshold in packets."""
        return self._ssthresh

    @property
    def state(self) -> CongestionState:
        """Current congestion control state."""
        return self._state

    @property
    def dup_acks(self) -> int:
        """Consecutive duplicate ACK count."""
        return self._dup_acks

    def window(self) -> int:
        """Effective sending window in integer packets."""
        return max(1, int(self._cwnd))

    def rto(self) -> float:
        """Current retransmission timeout in seconds."""
        return self._timeout

    def on_packet_sent(self, sequence: int, size: int = 1) -> None:
        """Hook called when a packet is transmitted."""
        del sequence, size

    def on_ack(self, sequence: int, rtt: float | None = None) -> None:
        """Process an acknowledgement for a new sequence number.

        Advances cwnd according to current state (Slow Start, Congestion Avoidance,
        or exits Fast Recovery).
        """
        # A fresh ACK resets the consecutive duplicate ACK counter
        self._dup_acks = 0
        self._update_rto(rtt, MIN_RTO, MAX_RTO)

        if self._state == CongestionState.FAST_RECOVERY and self._managed_recovery:
            # SACK recovery: cwnd stays at ssthresh until the loss event ends.
            return

        if self._state == CongestionState.FAST_RECOVERY:
            # Deflate window back to ssthresh and enter congestion avoidance (RFC 5681 3.2)
            self._cwnd = self._ssthresh
            self._state = CongestionState.CONGESTION_AVOIDANCE
            self._log_state_change("exit_fast_recovery", sequence=sequence, rtt=rtt)

        elif self._state == CongestionState.SLOW_START:
            # Slow start: cwnd += 1 per ACK (RFC 5681 3.1)
            self._cwnd = min(self._max_cwnd, self._cwnd + 1.0)
            if self._cwnd >= self._ssthresh:
                self._state = CongestionState.CONGESTION_AVOIDANCE
                self._log_state_change("enter_congestion_avoidance", sequence=sequence, rtt=rtt)
            else:
                self._log_state_change("slow_start_ack", sequence=sequence, rtt=rtt)

        elif self._state == CongestionState.CONGESTION_AVOIDANCE:
            # Congestion avoidance: cwnd += 1 / cwnd per ACK (RFC 5681 3.1)
            # This yields +1.0 packet increase per RTT window of ACKs
            increment = 1.0 / max(1.0, float(int(self._cwnd)))
            self._cwnd = min(self._max_cwnd, self._cwnd + increment)
            self._log_state_change("congestion_avoidance_ack", sequence=sequence, rtt=rtt)

    def on_duplicate_ack(self, ack_number: int) -> bool:
        """Process a duplicate ACK.

        Counts duplicate ACKs and triggers Fast Retransmit / Fast Recovery
        when the threshold (3 duplicate ACKs) is reached.
        """
        self._dup_acks += 1

        if self._dup_acks == self.DUP_ACK_THRESHOLD:
            # 3 Duplicate ACKs: Fast Retransmit & Fast Recovery (RFC 5681 3.2)
            self._ssthresh = max(2.0, self._cwnd / 2.0)
            self._cwnd = self._ssthresh + float(self.DUP_ACK_THRESHOLD)
            self._state = CongestionState.FAST_RECOVERY
            self._managed_recovery = False
            self._fast_recovery_seq = ack_number
            self._log_state_change("enter_fast_recovery", ack=ack_number)
            return True

        if self._state == CongestionState.FAST_RECOVERY:
            # Inflate window for each additional duplicate ACK
            self._cwnd = min(self._max_cwnd, self._cwnd + 1.0)
            self._log_state_change("fast_recovery_inflate", ack=ack_number)

        return False

    def on_loss(self, sequence: int) -> None:
        """Process a retransmission-timeout loss event."""
        del sequence
        self.on_rto_timeout()

    def on_fast_retransmit(self, sequence: int) -> None:
        """A SACK scoreboard declared ``sequence`` lost: halve the window once.

        The sender calls this once per window of data (not once per lost
        packet) and never collapses cwnd to 1 or backs off the RTO; that is
        reserved for retransmission timeouts. If the classic duplicate-ACK
        path already entered Fast Recovery this is a no-op.
        """
        if self._state == CongestionState.FAST_RECOVERY and not self._managed_recovery:
            return
        self._ssthresh = max(2.0, self._cwnd / 2.0)
        self._cwnd = self._ssthresh
        self._dup_acks = 0
        self._state = CongestionState.FAST_RECOVERY
        self._managed_recovery = True
        self._log_state_change("enter_fast_recovery", sequence=sequence, source="sack")

    def on_recovery_complete(self) -> None:
        """The cumulative ACK has passed the loss event: leave Fast Recovery."""
        if self._state == CongestionState.FAST_RECOVERY and self._managed_recovery:
            self._cwnd = self._ssthresh
            self._state = CongestionState.CONGESTION_AVOIDANCE
            self._managed_recovery = False
            self._log_state_change("exit_fast_recovery", source="sack")

    def on_rto_timeout(self) -> None:
        """Handle retransmission timeout: severe congestion signal.

        Per RFC 5681 Section 3.1:
          ssthresh = max(2, cwnd / 2)
          cwnd = 1 (or min_cwnd)
          state = Slow Start
        """
        self._ssthresh = max(2.0, self._cwnd / 2.0)
        self._cwnd = self._min_cwnd
        self._dup_acks = 0
        self._state = CongestionState.SLOW_START
        self._managed_recovery = False
        self._timeout = min(MAX_RTO, self._timeout * 2)
        self._log_state_change("timeout_loss")

    def _log_state_change(self, trigger: str, **extra: Any) -> None:
        """Log state update and notify observers."""
        record = {
            "time": time.monotonic(),
            "trigger": trigger,
            "cwnd": round(self._cwnd, 4),
            "window": self.window(),
            "ssthresh": round(self._ssthresh, 4),
            "state": self._state.value,
            "dup_acks": self._dup_acks,
            "rto": self._timeout,
            **extra,
        }
        self._history.append(record)

        if self._logger is not None:
            self._logger.log(
                "congestion_event",
                trigger=trigger,
                cwnd=self._cwnd,
                window=self.window(),
                ssthresh=self._ssthresh,
                state=self._state.value,
                dup_acks=self._dup_acks,
                rto=self._timeout,
                **extra,
            )

        self._notify_observers(record)

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of Reno controller state."""
        return {
            "type": "RenoController",
            "cwnd": self._cwnd,
            "window": self.window(),
            "ssthresh": self._ssthresh,
            "state": self._state.value,
            "dup_acks": self._dup_acks,
            "rto": self._timeout,
            "history_length": len(self._history),
        }

