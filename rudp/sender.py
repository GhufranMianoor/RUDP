"""Selective Repeat sender state machine with RTT sampling and congestion hooks."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, FLAG_DATA
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
    ) -> None:
        self.controller = controller or NullController(window_size, DEFAULT_RTO)
        self.rtt_sampler = rtt_sampler if rtt_sampler is not None else RTTSampler()
        self.base = initial_sequence
        self.next_sequence = initial_sequence
        self._outstanding: dict[int, _Outstanding] = {}

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
                packet = Packet(flags=FLAG_DATA, sequence=self.next_sequence, payload=chunk)
                now = time.monotonic()
                self._outstanding[packet.sequence] = _Outstanding(packet, now)
                self.rtt_sampler.on_packet_sent(packet.sequence, now)
                if hasattr(self.controller, "on_packet_sent"):
                    try:
                        self.controller.on_packet_sent(packet.sequence, len(chunk))
                    except Exception:
                        pass
                self.next_sequence += 1
                position += len(chunk)
                packets.append(packet)
            else:
                break
        return packets

    def can_send(self) -> bool:
        return self.in_flight < max(1, self.controller.window())

    def acknowledge(self, acknowledgement: int, sack_blocks: tuple[SACKBlock, ...] = ()) -> int:
        acknowledged = {
            sequence for sequence in self._outstanding if sequence < acknowledgement
        }
        for block in sack_blocks:
            acknowledged.update(
                sequence for sequence in self._outstanding
                if block.start <= sequence <= block.end
            )

        now = time.monotonic()

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

        while self.base not in self._outstanding and self.base < self.next_sequence:
            self.base += 1
        return len(acknowledged)

    def retransmissions(self) -> list[Packet]:
        now = time.monotonic()
        timeout = max(0.001, self.controller.rto())
        result: list[Packet] = []
        for sequence, item in self._outstanding.items():
            if now - item.sent_at >= timeout:
                item.sent_at = now
                item.retransmitted = True
                self.rtt_sampler.on_packet_retransmitted(sequence, now)
                self.controller.on_loss(sequence)
                result.append(item.packet)
        return result
