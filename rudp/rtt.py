"""RTT measurement and sampling hooks with Karn's algorithm.

This module provides round-trip time tracking for reliable UDP transport.
In accordance with RFC 6298 (Section 3 - Karn's Algorithm), RTT samples
are strictly collected ONLY for segments that have been transmitted exactly
once without retransmission, eliminating the retransmission ambiguity problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable

from .constants import MAX_RTO, MIN_RTO, RTO_VARIANCE_FLOOR


@dataclass(frozen=True, slots=True)
class RTTSample:
    """A single validated RTT measurement."""

    sequence: int
    rtt: float
    timestamp: float


@dataclass(slots=True)
class RTTStats:
    """Summary statistics for RTT measurements."""

    sample_count: int = 0
    ambiguous_count: int = 0
    latest_rtt: float | None = None
    min_rtt: float | None = None
    max_rtt: float | None = None
    mean_rtt: float | None = None
    smoothed_rtt: float | None = None
    rttvar: float | None = None
    rto: float = 1.0


@dataclass(slots=True)
class _TrackedPacket:
    """Internal metadata for a packet currently in flight."""

    sequence: int
    first_sent_time: float
    last_sent_time: float
    retransmitted: bool = False
    transmissions: int = 1


class RTTSampler:
    """Tracks packet transmissions and extracts valid RTT samples.

    Implements Karn's algorithm:
      - Packets acknowledged after being retransmitted are flagged as
        ambiguous and discarded from RTT estimation.
      - First-transmission packets that receive an acknowledgement produce
        a validated RTT sample.
    """

    def __init__(self) -> None:
        self._tracked: dict[int, _TrackedPacket] = {}
        self._stats = RTTStats()
        self._history: list[RTTSample] = []
        self._observers: list[Callable[[RTTSample, RTTStats], None]] = []

    @property
    def stats(self) -> RTTStats:
        """Return the current RTT statistics."""
        return self._stats

    @property
    def history(self) -> tuple[RTTSample, ...]:
        """Return an immutable snapshot of all collected RTT samples."""
        return tuple(self._history)

    @property
    def in_flight_count(self) -> int:
        """Return the number of tracked packets currently awaiting acknowledgement."""
        return len(self._tracked)

    def add_observer(self, callback: Callable[[RTTSample, RTTStats], None]) -> None:
        """Register a callback to be invoked whenever a new RTT sample is collected."""
        if callback not in self._observers:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[[RTTSample, RTTStats], None]) -> None:
        """Unregister an RTT sample callback."""
        if callback in self._observers:
            self._observers.remove(callback)

    def on_packet_sent(self, sequence: int, timestamp: float | None = None) -> None:
        """Record the transmission of a packet.

        If the packet is already tracked, this transmission is treated
        as a retransmission and marked as ambiguous per Karn's algorithm.
        """
        now = time.monotonic() if timestamp is None else float(timestamp)
        if sequence in self._tracked:
            item = self._tracked[sequence]
            item.retransmitted = True
            item.transmissions += 1
            item.last_sent_time = now
        else:
            self._tracked[sequence] = _TrackedPacket(
                sequence=sequence,
                first_sent_time=now,
                last_sent_time=now,
                retransmitted=False,
                transmissions=1,
            )

    def on_packet_retransmitted(self, sequence: int, timestamp: float | None = None) -> None:
        """Explicitly flag a packet as retransmitted.

        Future acknowledgements for this sequence will be ignored for
        RTT estimation under Karn's algorithm.
        """
        now = time.monotonic() if timestamp is None else float(timestamp)
        if sequence in self._tracked:
            item = self._tracked[sequence]
            item.retransmitted = True
            item.transmissions += 1
            item.last_sent_time = now
        else:
            # Packet was not previously tracked, track it as already retransmitted
            self._tracked[sequence] = _TrackedPacket(
                sequence=sequence,
                first_sent_time=now,
                last_sent_time=now,
                retransmitted=True,
                transmissions=2,
            )

    def on_packet_acked(self, sequence: int, timestamp: float | None = None) -> float | None:
        """Process an acknowledgement for a sequence number.

        Returns:
            The measured RTT (in seconds) if this was a valid first-transmission
            sample, or ``None`` if the packet was retransmitted (ambiguous) or
            was not tracked.
        """
        now = time.monotonic() if timestamp is None else float(timestamp)
        item = self._tracked.pop(sequence, None)
        if item is None:
            return None

        if item.retransmitted:
            self._stats.ambiguous_count += 1
            return None

        sample_rtt = max(0.0, now - item.first_sent_time)
        self._record_sample(sequence, sample_rtt, now)
        return sample_rtt

    def _record_sample(self, sequence: int, sample_rtt: float, timestamp: float) -> None:
        """Update statistics and notify observers of a valid sample."""
        st = self._stats
        st.sample_count += 1
        st.latest_rtt = sample_rtt
        if st.min_rtt is None or sample_rtt < st.min_rtt:
            st.min_rtt = sample_rtt
        if st.max_rtt is None or sample_rtt > st.max_rtt:
            st.max_rtt = sample_rtt
        if st.mean_rtt is None:
            st.mean_rtt = sample_rtt
        else:
            st.mean_rtt = (st.mean_rtt * (st.sample_count - 1) + sample_rtt) / st.sample_count
        if st.smoothed_rtt is None:
            st.smoothed_rtt = sample_rtt
            st.rttvar = sample_rtt / 2
        else:
            assert st.rttvar is not None
            st.rttvar = 0.75 * st.rttvar + 0.25 * abs(st.smoothed_rtt - sample_rtt)
            st.smoothed_rtt = 0.875 * st.smoothed_rtt + 0.125 * sample_rtt
        st.rto = min(MAX_RTO, max(MIN_RTO, st.smoothed_rtt + max(RTO_VARIANCE_FLOOR, 4 * st.rttvar)))

        sample = RTTSample(sequence=sequence, rtt=sample_rtt, timestamp=timestamp)
        self._history.append(sample)

        for observer in list(self._observers):
            try:
                observer(sample, st)
            except Exception:
                pass

    def is_tracked(self, sequence: int) -> bool:
        """Check if a sequence number is currently being tracked."""
        return sequence in self._tracked

    def is_retransmitted(self, sequence: int) -> bool:
        """Check if a sequence number has been flagged as retransmitted."""
        item = self._tracked.get(sequence)
        return item.retransmitted if item is not None else False

    def clear(self) -> None:
        """Reset all tracking and statistics."""
        self._tracked.clear()
        self._stats = RTTStats()
        self._history.clear()

