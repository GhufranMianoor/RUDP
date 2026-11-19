"""UDP connection and reliable file transfer helpers."""

from __future__ import annotations

import socket
import time
import random

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, FLAG_ACK, FLAG_DATA, FLAG_FIN, FLAG_SYN
from .eventlog import EventLogger
from .hooks import Controller
from .packet import Packet, PacketError
from .receiver import Receiver
from .sender import Sender


class ConnectionError(RuntimeError):
    """Raised when a connection cannot complete its protocol operation."""


class Connection:
    def __init__(
        self,
        sock: socket.socket,
        peer: tuple[str, int] | None = None,
        *,
        timeout: float = DEFAULT_RTO,
        window_size: int = DEFAULT_WINDOW,
        controller: Controller | None = None,
        connection_id: int | None = None,
        logger: EventLogger | None = None,
    ) -> None:
        self.sock = sock
        self.peer = peer
        self.timeout = timeout
        self.window_size = window_size
        self.controller = controller
        self.connection_id = (
            random.SystemRandom().randrange(1, 0x10000)
            if connection_id is None
            else int(connection_id)
        )
        self.logger = logger

    def _send(self, packet: Packet, peer: tuple[str, int] | None = None) -> None:
        target = peer or self.peer
        if target is None:
            raise ConnectionError("no peer address is configured")
        self.sock.sendto(packet.encode(), target)
        if self.logger is not None:
            self.logger.log("send", flags=packet.flags, sequence=packet.sequence)

    def _receive(self) -> tuple[Packet, tuple[str, int]]:
        data, peer = self.sock.recvfrom(65535)
        return Packet.decode(data), peer

    def connect(self, peer: tuple[str, int] | None = None) -> None:
        if peer is not None:
            self.peer = peer
        if self.peer is None:
            raise ConnectionError("connect requires a peer address")
        self.sock.settimeout(self.timeout)
        syn = Packet(
            flags=FLAG_SYN,
            connection_id=self.connection_id,
            sequence=0,
            timestamp=_timestamp_ms(),
        )
        for _ in range(5):
            self._send(syn)
            try:
                packet, address = self._receive()
                if (
                    address == self.peer
                    and packet.connection_id == self.connection_id
                    and packet.flags & FLAG_SYN
                    and packet.flags & FLAG_ACK
                ):
                    self._send(
                        Packet(
                            flags=FLAG_ACK,
                            connection_id=self.connection_id,
                            acknowledgement=1,
                            timestamp=_timestamp_ms(),
                            timestamp_echo=packet.timestamp,
                        )
                    )
                    return
            except (socket.timeout, ValueError):
                continue
        raise ConnectionError("handshake timed out")

    def send_bytes(self, data: bytes) -> None:
        sender = Sender(
            self.window_size,
            controller=self.controller,
            logger=self.logger,
            connection_id=self.connection_id,
        )
        position = 0
        self.sock.settimeout(self.timeout)
        while position < len(data) or sender.in_flight:
            for packet in sender.make_packets(data[position:]):
                position += len(packet.payload)
                self._send(packet)
            for packet in sender.retransmissions():
                self._send(packet)
            if sender.in_flight:
                try:
                    ack, address = self._receive()
                    if address == self.peer and ack.flags & FLAG_ACK:
                        if ack.connection_id != self.connection_id:
                            continue
                        sender.acknowledge(
                            ack.acknowledgement,
                            ack.sack_blocks,
                            ack.window,
                        )
                except (socket.timeout, PacketError):
                    pass
        self._send(
            Packet(
                flags=FLAG_FIN,
                connection_id=self.connection_id,
                sequence=sender.next_sequence,
                timestamp=_timestamp_ms(),
            )
        )

    def receive_bytes(self) -> bytes:
        receiver = Receiver(self.window_size)
        self.sock.settimeout(None)
        result = bytearray()
        established = False
        last_packet_at = time.monotonic()
        while True:
            try:
                packet, peer = self._receive()
            except PacketError:
                continue
            if self.peer is None:
                self.peer = peer
            if peer != self.peer:
                continue
            if packet.connection_id != self.connection_id:
                if not established and packet.flags & FLAG_SYN:
                    self.connection_id = packet.connection_id
                else:
                    continue
            last_packet_at = time.monotonic()
            if packet.flags & FLAG_FIN:
                self._send(
                    Packet(
                        flags=FLAG_ACK,
                        connection_id=self.connection_id,
                        acknowledgement=receiver.next_sequence,
                        window=max(0, self.window_size - receiver.buffered),
                        timestamp=_timestamp_ms(),
                        timestamp_echo=packet.timestamp,
                    )
                )
                self.sock.settimeout(max(self.timeout * 2, 0.1))
                while time.monotonic() - last_packet_at < max(self.timeout * 2, 0.1):
                    try:
                        final, final_peer = self._receive()
                    except (socket.timeout, PacketError):
                        break
                    if final_peer == self.peer and final.flags & FLAG_FIN:
                        self._send(
                            Packet(
                                flags=FLAG_ACK,
                                connection_id=self.connection_id,
                                acknowledgement=receiver.next_sequence,
                                timestamp=_timestamp_ms(),
                            )
                        )
                return bytes(result)
            if packet.flags & FLAG_SYN:
                established = True
                self._send(
                    Packet(
                        flags=FLAG_SYN | FLAG_ACK,
                        connection_id=self.connection_id,
                        acknowledgement=1,
                        timestamp=_timestamp_ms(),
                        timestamp_echo=packet.timestamp,
                    ),
                    peer,
                )
                continue
            if packet.flags & FLAG_DATA:
                data, ack = receiver.receive(packet)
                result.extend(data)
                ack = Packet(
                    flags=ack.flags,
                    connection_id=self.connection_id,
                    sequence=ack.sequence,
                    acknowledgement=ack.acknowledgement,
                    window=ack.window,
                    payload=ack.payload,
                    timestamp=_timestamp_ms(),
                    timestamp_echo=packet.timestamp,
                    sack_blocks=ack.sack_blocks,
                )
                self._send(ack, peer)

    def close(self) -> None:
        self.sock.close()


def _timestamp_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF
