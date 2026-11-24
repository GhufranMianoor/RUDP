"""Selective Repeat sender with a SACK scoreboard, RTT sampling and congestion hooks.

Design summary
--------------
* ``_outstanding`` holds every transmitted, not yet acknowledged packet.
* A packet is *acknowledged* when the cumulative ACK passes it or a SACK block
  covers it. Acknowledged packets leave ``_outstanding`` (the receiver never
  reneges) and SACKed sequence numbers are remembered in ``_sacked`` so that a
  hole can be declared lost once ``DUP_ACK_THRESHOLD`` packets above it have
  been SACKed (fast retransmit).
* A packet is *lost* when fast retransmit or the RTO says so. Lost packets are
  not "in flight": they stop counting against the window and wait in a queue
  until the window lets them be retransmitted. This keeps a timeout from
  turning into a full-window burst while cwnd is 1.
* Congestion control is told about a loss at most once per window of data
  (``_recovery_point``), and an RTO is reported once per timeout episode.
* Every transmission, including retransmissions, carries a fresh timestamp.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass, replace
import heapq
import time

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, DUP_ACK_THRESHOLD, FLAG_DATA
from .eventlog import EventLogger
from .hooks import Controller, NullController
from .packet import Packet, SACKBlock
from .rtt import RTTSampler

# Minimum spacing between full RTO scans of the outstanding set. The scan is
# O(window); 2 ms keeps timer error far below the 200 ms minimum RTO.
_SCAN_INTERVAL = 0.002


def _timestamp_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


@dataclass
class _Outstanding:
    packet: Packet
    sent_at: float
    retransmitted: bool = False
    lost: bool = False
    # Retransmit immediately even if the window is full (head of a fast
    # retransmit loss event).
    force: bool = False
    # Reason for the pending retransmission: "timeout" or "fast_retransmit".
    reason: str = ""
    # next_sequence when this packet was last retransmitted; used to detect a
    # lost retransmission (3 packets sent afterwards were SACKed first).
    retx_high: int = 0


class Sender:
    """Builds data packets, tracks selective acknowledgements, and samples RTT."""

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW,
        controller: Controller | None = None,
        initial_sequence: int = 1,
        rtt_sampler: RTTSampler | None = None,
        logger: EventLogger | None = None,
        connection_id: int = 0,
    ) -> None:
        self.controller = controller or NullController(window_size, DEFAULT_RTO)
        self.rtt_sampler = rtt_sampler if rtt_sampler is not None else RTTSampler()
        self.logger = logger
        self.connection_id = int(connection_id)
        self.max_span = max(1, int(window_size))
        self.remote_window = self.max_span
        self.base = initial_sequence
        self.next_sequence = initial_sequence
        self._outstanding: dict[int, _Outstanding] = {}
        self._sacked: list[int] = []  # sorted, all > cumulative ACK
        self._sacked_set: set[int] = set()
        self._lost: set[int] = set()
        self._lost_heap: list[int] = []  # lost, not forced, awaiting window
        self._forced: list[int] = []
        self._recovery_point = 0  # no loss event may be reported below this
        self._rto_point = 0  # no RTO congestion event may be reported below this
        self._in_recovery = False
        self._next_scan = 0.0
        self._earliest_deadline: float | None = None
        self.stats = {"fast_retransmits": 0, "timeouts": 0, "retransmissions": 0}

    # ------------------------------------------------------------------ state

    @property
    def in_flight(self) -> int:
        """Packets believed to be in the network (excludes packets marked lost)."""

        return len(self._outstanding) - len(self._lost)

    @property
    def unacked(self) -> int:
        """All packets not yet acknowledged, including those awaiting retransmission."""

        return len(self._outstanding)

    def _window_limit(self) -> int:
        if self.remote_window == 0:
            return 1
        return min(max(1, self.controller.window()), self.remote_window)

    def can_send(self) -> bool:
        if self.next_sequence - self.base >= self.max_span:
            return False
        if self.remote_window == 0:
            return self.in_flight == 0
        return self.in_flight < self._window_limit()

    def set_remote_window(self, window: int) -> None:
        self.remote_window = max(0, int(window))

    # ------------------------------------------------------------- new data

    def make_packets(
        self, data: bytes | memoryview, *, chunk_size: int = 1200, start: int = 0
    ) -> list[Packet]:
        """Build as many new packets from ``data[start:]`` as the window allows.

        ``start`` lets callers pass the whole buffer instead of ``data[pos:]``,
        which would copy the remaining file on every call.
        """

        if not 1 <= chunk_size <= 1200:
            raise ValueError("chunk_size must be between 1 and 1200")
        packets: list[Packet] = []
        position = start
        view = data if isinstance(data, memoryview) else memoryview(data)
        total = len(view)
        while position < total and self.can_send():
            chunk = bytes(view[position : position + chunk_size])
            now = time.monotonic()
            packet = Packet(
                flags=FLAG_DATA,
                connection_id=self.connection_id,
                sequence=self.next_sequence,
                payload=chunk,
                timestamp=_timestamp_ms(),
            )
            self._outstanding[packet.sequence] = _Outstanding(packet, now)
            self.rtt_sampler.on_packet_sent(packet.sequence, now)
            on_sent = getattr(self.controller, "on_packet_sent", None)
            if on_sent is not None:
                try:
                    on_sent(packet.sequence, len(chunk))
                except Exception:
                    pass
            if self.logger is not None:
                self.logger.log("send", sequence=packet.sequence, size=len(chunk))
            self.next_sequence += 1
            position += len(chunk)
            packets.append(packet)
        return packets

    # ------------------------------------------------------------- ACK path

    def acknowledge(
        self,
        acknowledgement: int,
        sack_blocks: tuple[SACKBlock, ...] = (),
        remote_window: int | None = None,
    ) -> int:
        if remote_window is not None:
            self.set_remote_window(remote_window)
        now = time.monotonic()
        highest = self.next_sequence - 1

        newly: list[int] = []
        for sequence in range(self.base, min(acknowledgement, self.next_sequence)):
            if sequence in self._outstanding:
                newly.append(sequence)
        new_sack_info = False
        for block in sack_blocks:
            for sequence in range(max(block.start, acknowledgement), min(block.end, highest) + 1):
                if sequence in self._sacked_set:
                    continue
                if sequence in self._outstanding:
                    newly.append(sequence)
                    self._sacked_set.add(sequence)
                    insort(self._sacked, sequence)
                    new_sack_info = True

        if not newly:
            self._duplicate_ack(acknowledgement, sack_blocks)
            return 0

        for sequence in newly:
            item = self._outstanding.pop(sequence)
            if item.lost:
                self._lost.discard(sequence)
            rtt_sample = self.rtt_sampler.on_packet_acked(sequence, now)
            self.controller.on_ack(sequence, rtt_sample)
            if self.logger is not None:
                self.logger.log(
                    "ack", sequence=sequence, acknowledgement=acknowledgement, rtt=rtt_sample
                )
                if rtt_sample is not None:
                    self.logger.log("rtt", sequence=sequence, rtt=rtt_sample)

        while self.base not in self._outstanding and self.base < self.next_sequence:
            self.base += 1
        # Forget SACK state below the cumulative ACK.
        cut = bisect_left(self._sacked, self.base)
        if cut:
            for sequence in self._sacked[:cut]:
                self._sacked_set.discard(sequence)
            del self._sacked[:cut]

        if new_sack_info:
            self._detect_sack_losses()

        if self._in_recovery and self.base >= self._recovery_point:
            self._in_recovery = False
            hook = getattr(self.controller, "on_recovery_complete", None)
            if hook is not None:
                hook()
            if self.logger is not None:
                self.logger.log("recovery_complete", base=self.base)
        return len(newly)

    def _duplicate_ack(self, acknowledgement: int, sack_blocks: tuple[SACKBlock, ...]) -> None:
        """An ACK that acknowledges nothing new.

        With SACK in use, loss detection is driven by the scoreboard, so only
        a SACK-free ACK that repeats the cumulative ACK counts as the classic
        duplicate ACK of RFC 5681.
        """

        if sack_blocks or acknowledgement != self.base or not self._outstanding:
            return
        hook = getattr(self.controller, "on_duplicate_ack", None)
        if hook is None:
            return
        try:
            triggered = hook(acknowledgement)
        except Exception:
            return
        if triggered and self.base in self._outstanding and self.base not in self._lost:
            self._recovery_point = self.next_sequence
            self._in_recovery = True
            self._mark_lost(self.base, "fast_retransmit", force=True)

    def _detect_sack_losses(self) -> None:
        if len(self._sacked) < DUP_ACK_THRESHOLD:
            return
        limit = self._sacked[-DUP_ACK_THRESHOLD]  # holes below this have >= 3 SACKs above
        for sequence in range(self.base, limit):
            item = self._outstanding.get(sequence)
            if item is None or item.lost:
                continue
            threshold = max(sequence, item.retx_high - 1) if item.retransmitted else sequence
            above = len(self._sacked) - bisect_right(self._sacked, threshold)
            if above < DUP_ACK_THRESHOLD:
                continue
            new_event = sequence >= self._recovery_point
            if new_event:
                # One congestion response per window of data.
                self._recovery_point = self.next_sequence
                self._in_recovery = True
                hook = getattr(self.controller, "on_fast_retransmit", None)
                if hook is not None:
                    try:
                        hook(sequence)
                    except Exception:
                        pass
            self._mark_lost(sequence, "fast_retransmit", force=new_event)

    def _mark_lost(self, sequence: int, reason: str, *, force: bool = False) -> None:
        item = self._outstanding[sequence]
        item.lost = True
        item.reason = reason
        item.force = force
        self._lost.add(sequence)
        if force:
            self._forced.append(sequence)
        else:
            heapq.heappush(self._lost_heap, sequence)
        # Karn: an ACK after this point is ambiguous.
        self.rtt_sampler.on_packet_retransmitted(sequence, time.monotonic())
        if reason == "fast_retransmit":
            self.stats["fast_retransmits"] += 1
        if self.logger is not None:
            self.logger.log("loss", sequence=sequence, kind=reason)

    # ------------------------------------------------------- retransmission

    def time_to_next_timeout(self) -> float:
        """Seconds until the earliest RTO could fire (upper bound for a socket wait)."""

        if self._forced or (self._lost and self.in_flight < self._window_limit()):
            return 0.0
        if self._earliest_deadline is None:
            return 0.05
        return max(0.0, self._earliest_deadline - time.monotonic())

    def retransmissions(self) -> list[Packet]:
        now = time.monotonic()
        timeout = max(0.001, self.controller.rto())

        if now >= self._next_scan:
            self._next_scan = now + _SCAN_INTERVAL
            timed_out: list[int] = []
            earliest: float | None = None
            for sequence, item in self._outstanding.items():
                if item.lost:
                    continue
                deadline = item.sent_at + timeout
                if deadline <= now:
                    timed_out.append(sequence)
                elif earliest is None or deadline < earliest:
                    earliest = deadline
            self._earliest_deadline = earliest
            if timed_out:
                if max(timed_out) >= self._rto_point:
                    # One congestion response per timeout episode.
                    self.stats["timeouts"] += 1
                    self._rto_point = self.next_sequence
                    self._recovery_point = self.next_sequence
                    self._in_recovery = True
                    self.controller.on_loss(timed_out[0])
                for sequence in timed_out:
                    if self.logger is not None:
                        self.logger.log("timeout", sequence=sequence, rto=timeout)
                    self._mark_lost(sequence, "timeout")

        result: list[Packet] = []
        while self._forced:
            sequence = self._forced.pop(0)
            if sequence in self._lost:
                result.append(self._retransmit(sequence, now))
        limit = self._window_limit()
        while self._lost_heap and self.in_flight < limit:
            sequence = heapq.heappop(self._lost_heap)
            if sequence in self._lost:
                result.append(self._retransmit(sequence, now))
        return result

    def _retransmit(self, sequence: int, now: float) -> Packet:
        item = self._outstanding[sequence]
        item.packet = replace(item.packet, timestamp=_timestamp_ms())  # fresh stamp
        item.sent_at = now
        item.retransmitted = True
        item.lost = False
        item.force = False
        item.retx_high = self.next_sequence
        self._lost.discard(sequence)
        self.rtt_sampler.on_packet_retransmitted(sequence, now)
        self.stats["retransmissions"] += 1
        # The new copy has a fresh deadline; the cached earliest deadline is
        # only an upper bound for waiting, so leaving it is safe.
        if self.logger is not None:
            self.logger.log("retx", sequence=sequence, reason=item.reason)
        return item.packet
