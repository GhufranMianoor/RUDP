"""UDP connection and reliable file transfer helpers."""

from __future__ import annotations

import socket
import time

from .constants import DEFAULT_RTO, DEFAULT_WINDOW, FLAG_ACK, FLAG_FIN, FLAG_SYN
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
    ) -> None:
        self.sock = sock
        self.peer = peer
        self.timeout = timeout
        self.window_size = window_size

    def _send(self, packet: Packet, peer: tuple[str, int] | None = None) -> None:
        target = peer or self.peer
        if target is None:
            raise ConnectionError("no peer address is configured")
        self.sock.sendto(packet.encode(), target)

    def _receive(self) -> tuple[Packet, tuple[str, int]]:
        data, peer = self.sock.recvfrom(65535)
        return Packet.decode(data), peer

    def connect(self, peer: tuple[str, int] | None = None) -> None:
        if peer is not None:
            self.peer = peer
        if self.peer is None:
            raise ConnectionError("connect requires a peer address")
        self.sock.settimeout(self.timeout)
        syn = Packet(flags=FLAG_SYN, sequence=0)
        for _ in range(5):
            self._send(syn)
            try:
                packet, address = self._receive()
                if address == self.peer and packet.flags & FLAG_SYN and packet.flags & FLAG_ACK:
                    self._send(Packet(flags=FLAG_ACK, acknowledgement=1))
                    return
            except (socket.timeout, ValueError):
                continue
        raise ConnectionError("handshake timed out")

    def send_bytes(self, data: bytes) -> None:
        sender = Sender(self.window_size)
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
                        sender.acknowledge(ack.acknowledgement, ack.sack_blocks)
                except (socket.timeout, PacketError):
                    pass
        self._send(Packet(flags=FLAG_FIN, sequence=sender.next_sequence))

    def receive_bytes(self) -> bytes:
        receiver = Receiver(self.window_size)
        self.sock.settimeout(None)
        result = bytearray()
        while True:
            try:
                packet, peer = self._receive()
            except PacketError:
                continue
            if self.peer is None:
                self.peer = peer
            if peer != self.peer:
                continue
            if packet.flags & FLAG_FIN:
                self._send(Packet(flags=FLAG_ACK, acknowledgement=receiver.next_sequence))
                return bytes(result)
            if packet.flags & FLAG_SYN:
                self._send(Packet(flags=FLAG_SYN | FLAG_ACK, acknowledgement=1), peer)
                continue
            if packet.flags:
                data, ack = receiver.receive(packet)
                result.extend(data)
                self._send(ack, peer)

    def close(self) -> None:
        self.sock.close()
