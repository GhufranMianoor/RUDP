"""UDP connection and reliable file transfer helpers."""

from __future__ import annotations

import random
import socket
import time
from typing import Callable

from .constants import (
    DEFAULT_RTO,
    DEFAULT_WINDOW,
    FLAG_ACK,
    FLAG_DATA,
    FLAG_FIN,
    FLAG_SYN,
)
from .eventlog import EventLogger
from .hooks import Controller
from .packet import Packet, PacketError
from .receiver import Receiver
from .sender import Sender

# Connection set-up and tear-down retry limits.
SYN_ATTEMPTS = 7
FIN_ATTEMPTS = 8
# A sender that makes no progress for this long gives up instead of hanging.
DEFAULT_STALL_TIMEOUT = 30.0
# A receiver that hears nothing for this long gives up instead of hanging.
DEFAULT_IDLE_TIMEOUT = 30.0
# After acknowledging a FIN the receiver keeps answering retransmitted FINs
# for this long (a miniature TIME_WAIT) so a lost FIN-ACK cannot strand the sender.
DEFAULT_LINGER = 1.5


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
        idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT,
        stall_timeout: float | None = DEFAULT_STALL_TIMEOUT,
        linger: float = DEFAULT_LINGER,
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
        self.idle_timeout = idle_timeout
        self.stall_timeout = stall_timeout
        self.linger = linger
        self.sender: Sender | None = None

    # ------------------------------------------------------------------ I/O

    def _send(self, packet: Packet, peer: tuple[str, int] | None = None) -> None:
        target = peer or self.peer
        if target is None:
            raise ConnectionError("no peer address is configured")
        self.sock.sendto(packet.encode(), target)
        # DATA sends and retransmissions are logged by the Sender; log the
        # control packets (SYN / FIN / ACK) here.
        if self.logger is not None and not packet.flags & FLAG_DATA:
            self.logger.log(
                "ctrl", flags=packet.flags, sequence=packet.sequence, ack=packet.acknowledgement
            )

    def _receive(self) -> tuple[Packet, tuple[str, int]]:
        data, peer = self.sock.recvfrom(65535)
        return Packet.decode(data), peer

    def _receive_until(self, deadline: float) -> tuple[Packet, tuple[str, int]] | None:
        """Receive one valid packet before ``deadline`` (monotonic), else None."""

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                return self._receive()
            except socket.timeout:
                return None
            except PacketError:
                continue
            except ConnectionRefusedError:
                time.sleep(min(0.05, max(0.0, remaining)))
                continue

    # ------------------------------------------------------------ handshake

    def connect(self, peer: tuple[str, int] | None = None) -> None:
        if peer is not None:
            self.peer = peer
        if self.peer is None:
            raise ConnectionError("connect requires a peer address")
        wait = self.timeout
        first_sent = 0.0
        for attempt in range(SYN_ATTEMPTS):
            syn = Packet(
                flags=FLAG_SYN,
                connection_id=self.connection_id,
                sequence=0,
                timestamp=_timestamp_ms(),
            )
            sent_at = time.monotonic()
            if attempt == 0:
                first_sent = sent_at
            self._send(syn)
            deadline = sent_at + wait
            while True:
                received = self._receive_until(deadline)
                if received is None:
                    break
                packet, address = received
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
                    # Karn: only a SYN that was never retransmitted gives a
                    # valid first RTT sample; it seeds the RTO estimate.
                    if attempt == 0:
                        observe = getattr(self.controller, "observe_rtt", None)
                        if observe is not None:
                            observe(time.monotonic() - first_sent)
                    return
            wait = min(wait * 2, 4.0)
        raise ConnectionError("handshake timed out")

    # ----------------------------------------------------------------- send

    def send_bytes(
        self,
        data: bytes,
        progress: Callable[[Sender], None] | None = None,
        progress_interval: float = 0.5,
    ) -> None:
        """Send ``data`` reliably, then close with FIN.

        ``progress`` (optional) is called with the Sender about every
        ``progress_interval`` seconds, e.g. to print cwnd live during a demo.
        """

        sender = Sender(
            self.window_size,
            controller=self.controller,
            logger=self.logger,
            connection_id=self.connection_id,
        )
        self.sender = sender
        view = memoryview(data)
        total = len(view)
        position = 0
        last_progress = time.monotonic()
        last_base = sender.base
        next_progress = time.monotonic() + progress_interval
        while position < total or sender.unacked:
            for packet in sender.retransmissions():
                self._send(packet)
            for packet in sender.make_packets(view, start=position):
                position += len(packet.payload)
                self._send(packet)
            if not sender.unacked:
                continue
            wait = min(0.05, max(0.001, sender.time_to_next_timeout()))
            self.sock.settimeout(wait)
            try:
                ack, address = self._receive()
            except socket.timeout:
                ack = None
            except (PacketError, ConnectionRefusedError):
                ack = None
            if (
                ack is not None
                and address == self.peer
                and ack.connection_id == self.connection_id
                and ack.flags & FLAG_ACK
                and not ack.flags & (FLAG_SYN | FLAG_FIN)
            ):
                sender.acknowledge(ack.acknowledgement, ack.sack_blocks, ack.window)
            now = time.monotonic()
            if progress is not None and now >= next_progress:
                next_progress = now + progress_interval
                progress(sender)
            if sender.base != last_base:
                last_base = sender.base
                last_progress = now
            elif self.stall_timeout and now - last_progress > self.stall_timeout:
                raise ConnectionError(
                    f"no progress for {self.stall_timeout:.0f}s; receiver unreachable"
                )
        self._close_sender(sender)

    def _close_sender(self, sender: Sender) -> None:
        """Send a FIN and retransmit it until the receiver confirms with FIN|ACK."""

        wait = min(1.0, max(0.2, sender.controller.rto()))
        for _ in range(FIN_ATTEMPTS):
            fin = Packet(
                flags=FLAG_FIN,
                connection_id=self.connection_id,
                sequence=sender.next_sequence,
                timestamp=_timestamp_ms(),
            )
            self._send(fin)
            deadline = time.monotonic() + wait
            while True:
                received = self._receive_until(deadline)
                if received is None:
                    break
                ack, address = received
                if (
                    address == self.peer
                    and ack.connection_id == self.connection_id
                    and ack.flags & FLAG_ACK
                    and ack.flags & FLAG_FIN
                    and ack.acknowledgement >= sender.next_sequence
                ):
                    return
        raise ConnectionError("FIN acknowledgement timed out")

    # -------------------------------------------------------------- receive

    def receive_bytes(self) -> bytes:
        receiver = Receiver(self.window_size)
        result = bytearray()
        established = False
        last_activity = time.monotonic()
        poll = 1.0 if not self.idle_timeout else min(1.0, self.idle_timeout)
        self.sock.settimeout(poll)
        while True:
            try:
                packet, peer = self._receive()
            except socket.timeout:
                if self.idle_timeout and time.monotonic() - last_activity > self.idle_timeout:
                    raise ConnectionError(
                        f"receiver idle for {self.idle_timeout:.0f}s; sender gone"
                    ) from None
                continue
            except (PacketError, ConnectionRefusedError):
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
            last_activity = time.monotonic()

            if packet.flags & FLAG_FIN:
                if packet.sequence != receiver.next_sequence:
                    # FIN ahead of missing data: keep waiting for the data.
                    self._send_ack(receiver, packet, peer)
                    continue
                self._finish(receiver, packet)
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
                data, _ = receiver.receive(packet)
                result.extend(data)
                self._send_ack(receiver, packet, peer)

    def _send_ack(self, receiver: Receiver, trigger: Packet, peer: tuple[str, int]) -> None:
        ack = receiver.ack(
            connection_id=self.connection_id,
            timestamp=_timestamp_ms(),
            timestamp_echo=trigger.timestamp,
        )
        self._send(ack, peer)

    def _finish(self, receiver: Receiver, fin: Packet) -> None:
        """Acknowledge the FIN and linger so a lost FIN|ACK can be repeated."""

        def fin_ack(trigger: Packet) -> Packet:
            return Packet(
                flags=FLAG_ACK | FLAG_FIN,
                connection_id=self.connection_id,
                acknowledgement=receiver.next_sequence,
                window=max(0, self.window_size - receiver.buffered),
                timestamp=_timestamp_ms(),
                timestamp_echo=trigger.timestamp,
            )

        self._send(fin_ack(fin))
        deadline = time.monotonic() + self.linger
        while True:
            received = self._receive_until(deadline)
            if received is None:
                return
            final, final_peer = received
            if (
                final_peer == self.peer
                and final.connection_id == self.connection_id
                and final.flags & FLAG_FIN
            ):
                self._send(fin_ack(final))
                deadline = time.monotonic() + self.linger

    def close(self) -> None:
        self.sock.close()


def _timestamp_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF
