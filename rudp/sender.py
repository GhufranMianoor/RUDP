"""Selective Repeat sender state machine with RTT sampling and congestion hooks."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, FLAG_DATA
from .eventlog import EventLogger
from .hooks import Controller, NullController
from .packet import Packet, SACKBlock
from .rtt import RTTSampler


@dataclass
class _Outstanding:
    packet: Packet
    sent_at: float
    retransmitted: bool = False


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
        self.remote_window = max(1, int(window_size))
        self.base = initial_sequence
        self.next_sequence = initial_sequence
        self._outstanding: dict[int, _Outstanding] = {}
        self._fast_retransmit: set[int] = set()
        self._higher_acks: dict[int, set[int]] = {}

    @property
    def in_flight(self) -> int:
        return len(self._outstanding)

    def make_packets(self, data: bytes, *, chunk_size: int = 1200) -> list[Packet]:
        if not 1 <= chunk_size <= 1200:
            raise ValueError("chunk_size must be between 1 and 1200")
        packets: list[Packet] = []
        position = 0
        while position < len(data):
            if self.can_send():
                chunk = data[position : position + chunk_size]
                packet = Packet(
                    flags=FLAG_DATA,
                    connection_id=self.connection_id,
                    sequence=self.next_sequence,
                    payload=chunk,
                    timestamp=int(time.monotonic() * 1000) & 0xFFFFFFFF,
                )
                now = time.monotonic()
                self._outstanding[packet.sequence] = _Outstanding(packet, now)
                self.rtt_sampler.on_packet_sent(packet.sequence, now)
                if hasattr(self.controller, "on_packet_sent"):
                    try:
                        self.controller.on_packet_sent(packet.sequence, len(chunk))
                    except Exception:
                        pass
                if self.logger is not None:
                    self.logger.log("send", sequence=packet.sequence, size=len(chunk))
                self.next_sequence += 1
                position += len(chunk)
                packets.append(packet)
            else:
                break
        return packets

    def can_send(self) -> bool:
        if self.remote_window == 0:
            return self.in_flight == 0
        return self.in_flight < min(max(1, self.controller.window()), self.remote_window)

    def set_remote_window(self, window: int) -> None:
        self.remote_window = max(0, int(window))

    def acknowledge(
        self,
        acknowledgement: int,
        sack_blocks: tuple[SACKBlock, ...] = (),
        remote_window: int | None = None,
    ) -> int:
        if remote_window is not None:
            self.set_remote_window(remote_window)
        acknowledged = {
            sequence for sequence in self._outstanding if sequence < acknowledgement
        }
        for block in sack_blocks:
            acknowledged.update(
                sequence for sequence in self._outstanding
                if block.start <= sequence <= block.end
            )

        now = time.monotonic()
        base_before_ack = self.base

        # If no outstanding packets were acknowledged, this is a duplicate ACK
        if not acknowledged:
            if hasattr(self.controller, "on_duplicate_ack"):
                try:
                    self.controller.on_duplicate_ack(acknowledgement)
                except Exception:
                    pass
            return 0

        # Process newly acknowledged packets
        for sequence in acknowledged:
            item = self._outstanding.pop(sequence)
            # Karn's algorithm: rtt_sampler returns None if retransmitted
            rtt_sample = self.rtt_sampler.on_packet_acked(sequence, now)
            self.controller.on_ack(sequence, rtt_sample)
            if self.logger is not None:
                self.logger.log(
                    "ack",
                    sequence=sequence,
                    acknowledgement=acknowledgement,
                    rtt=rtt_sample,
                )
                if rtt_sample is not None:
                    self.logger.log("rtt", sequence=sequence, rtt=rtt_sample)

        if base_before_ack in self._outstanding:
            higher = self._higher_acks.setdefault(base_before_ack, set())
            higher.update(sequence for sequence in acknowledged if sequence > base_before_ack)
            if len(higher) >= 3:
                self._fast_retransmit.add(base_before_ack)
                self._higher_acks.pop(base_before_ack, None)

        while self.base not in self._outstanding and self.base < self.next_sequence:
            self._higher_acks.pop(self.base, None)
            self.base += 1
        return len(acknowledged)

    def retransmissions(self) -> list[Packet]:
        now = time.monotonic()
        timeout = max(0.001, self.controller.rto())
        result: list[Packet] = []
        for sequence, item in self._outstanding.items():
            fast_retransmit = sequence in self._fast_retransmit
            timed_out = now - item.sent_at >= timeout
            if fast_retransmit or timed_out:
                self._fast_retransmit.discard(sequence)
                item.sent_at = now
                item.retransmitted = True
                self.rtt_sampler.on_packet_retransmitted(sequence, now)
                self.controller.on_loss(sequence)
                if self.logger is not None:
                    if timed_out:
                        self.logger.log("timeout", sequence=sequence, rto=timeout)
                    self.logger.log(
                        "retx",
                        sequence=sequence,
                        reason="timeout" if timed_out else "fast_retransmit",
                    )
                result.append(item.packet)
        return result
