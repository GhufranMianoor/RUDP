"""Selective Repeat receiver state machine."""

from __future__ import annotations

from collections import deque

from .constants import DEFAULT_WINDOW, FLAG_ACK
from .packet import MAX_SACK_BLOCKS, Packet, SACKBlock


class Receiver:
    """Reorders data packets and produces cumulative ACKs with SACK ranges.

    The packet format carries at most :data:`MAX_SACK_BLOCKS` SACK blocks.
    Following RFC 2018, the first block describes the most recently received
    out-of-order data and the remaining blocks the next most recent ones, so
    the sender's scoreboard converges even when there are more gaps than
    blocks.
    """

    def __init__(self, window_size: int = DEFAULT_WINDOW, initial_sequence: int = 1) -> None:
        self.window_size = max(1, int(window_size))
        self.next_sequence = initial_sequence
        self._buffer: dict[int, bytes] = {}
        self._recent: deque[int] = deque(maxlen=64)

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def receive(self, packet: Packet) -> tuple[bytes, Packet]:
        if packet.sequence < self.next_sequence:
            return b"", self.ack()
        if packet.sequence >= self.next_sequence + self.window_size:
            return b"", self.ack()
        self._buffer.setdefault(packet.sequence, packet.payload)
        self._recent.append(packet.sequence)
        output = bytearray()
        while self.next_sequence in self._buffer:
            output.extend(self._buffer.pop(self.next_sequence))
            self.next_sequence += 1
        return bytes(output), self.ack()

    def sack_blocks(self) -> tuple[SACKBlock, ...]:
        """Return up to three inclusive ranges, most recently touched first."""

        blocks: list[SACKBlock] = []
        for sequence in reversed(self._recent):
            if sequence not in self._buffer:
                continue
            if any(block.start <= sequence <= block.end for block in blocks):
                continue
            start = sequence
            while start - 1 in self._buffer:
                start -= 1
            end = sequence
            while end + 1 in self._buffer:
                end += 1
            blocks.append(SACKBlock(start, end))
            if len(blocks) == MAX_SACK_BLOCKS:
                break
        return tuple(blocks)

    def ack(
        self,
        *,
        connection_id: int = 0,
        timestamp_echo: int = 0,
        timestamp: int = 0,
    ) -> Packet:
        return Packet(
            flags=FLAG_ACK,
            connection_id=connection_id,
            acknowledgement=self.next_sequence,
            window=max(0, min(0xFFFF, self.window_size - len(self._buffer))),
            timestamp=timestamp,
            timestamp_echo=timestamp_echo,
            sack_blocks=self.sack_blocks(),
        )
