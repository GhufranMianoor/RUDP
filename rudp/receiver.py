"""Selective Repeat receiver state machine."""

from __future__ import annotations

from .constants import DEFAULT_WINDOW, FLAG_ACK
from .packet import Packet, SACKBlock


class Receiver:
    """Reorders data packets and produces cumulative ACKs with SACK ranges."""

    def __init__(self, window_size: int = DEFAULT_WINDOW, initial_sequence: int = 1) -> None:
        self.window_size = max(1, int(window_size))
        self.next_sequence = initial_sequence
        self._buffer: dict[int, bytes] = {}

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def receive(self, packet: Packet) -> tuple[bytes, Packet]:
        if packet.sequence < self.next_sequence:
            return b"", self.ack()
        if packet.sequence >= self.next_sequence + self.window_size:
            return b"", self.ack()
        self._buffer.setdefault(packet.sequence, packet.payload)
        output = bytearray()
        while self.next_sequence in self._buffer:
            output.extend(self._buffer.pop(self.next_sequence))
            self.next_sequence += 1
        return bytes(output), self.ack()

    def ack(
        self,
        *,
        connection_id: int = 0,
        timestamp_echo: int = 0,
        timestamp: int = 0,
    ) -> Packet:
        blocks: list[SACKBlock] = []
        for sequence in sorted(self._buffer):
            if blocks and sequence == blocks[-1].end + 1:
                blocks[-1] = SACKBlock(blocks[-1].start, sequence)
            else:
                blocks.append(SACKBlock(sequence, sequence))
        return Packet(
            flags=FLAG_ACK,
            connection_id=connection_id,
            acknowledgement=self.next_sequence,
            window=max(0, self.window_size - len(self._buffer)),
            timestamp=timestamp,
            timestamp_echo=timestamp_echo,
            sack_blocks=tuple(blocks),
        )
