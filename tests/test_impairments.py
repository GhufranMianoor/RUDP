"""End-to-end transfers through the impairment proxy (localhost, no Mininet).

Every transfer runs against a watchdog, so a protocol hang fails the test
instead of hanging the suite.
"""

from __future__ import annotations

import hashlib
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import Connection, ConnectionError
from rudp.constants import FLAG_ACK, FLAG_FIN, FLAG_SYN
from rudp.controller import RenoController
from tools.lossy_proxy import FORWARD, REVERSE, LossyProxy

WATCHDOG = 60.0


def drop_first(direction: str, flags: int, count: int = 1):
    """Hook that drops the first ``count`` packets in ``direction`` whose flag byte equals ``flags``."""

    remaining = [count]

    def hook(seen_direction: str, data: bytes) -> bool:
        if seen_direction == direction and len(data) > 1 and data[1] == flags and remaining[0] > 0:
            remaining[0] -= 1
            return True
        return False

    return hook


class ImpairmentTests(unittest.TestCase):
    def transfer(self, size: int = 200_000, *, payload: bytes | None = None, **proxy_args):
        payload = os.urandom(size) if payload is None else payload
        outcome: dict = {}

        def receive(sock: socket.socket) -> None:
            try:
                outcome["data"] = Connection(sock, idle_timeout=20.0, linger=1.0).receive_bytes()
            except Exception as exc:  # reported through the assertion below
                outcome["receiver_error"] = repr(exc)

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        thread = threading.Thread(target=receive, args=(recv_sock,), daemon=True)
        thread.start()
        started = time.monotonic()
        with LossyProxy(recv_sock.getsockname(), **proxy_args) as proxy:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                connection = Connection(
                    sock,
                    controller=RenoController(initial_ssthresh=1024.0),
                    stall_timeout=20.0,
                )
                connection.connect(("127.0.0.1", proxy.port))
                connection.send_bytes(payload)
            thread.join(timeout=WATCHDOG)
            elapsed = time.monotonic() - started
            recv_sock.close()
            self.assertFalse(thread.is_alive(), "receiver did not exit (hang)")
            self.assertNotIn("receiver_error", outcome, outcome.get("receiver_error"))
            self.assertEqual(
                hashlib.sha256(outcome["data"]).hexdigest(),
                hashlib.sha256(payload).hexdigest(),
            )
            return connection, proxy, elapsed

    def test_loss_2_percent(self) -> None:
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                self.transfer(300_000, drop=0.02, seed=seed)

    def test_loss_5_percent(self) -> None:
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                connection, proxy, _ = self.transfer(300_000, drop=0.05, seed=seed)
                self.assertGreater(proxy.stats["dropped"], 0)
                self.assertGreater(connection.sender.stats["retransmissions"], 0)

    def test_heavy_loss_many_gaps(self) -> None:
        # 20% loss routinely produces more than three simultaneous gaps.
        self.transfer(60_000, drop=0.20, seed=7)

    def test_duplication(self) -> None:
        self.transfer(200_000, duplicate=0.2, seed=4)

    def test_reordering(self) -> None:
        self.transfer(200_000, reorder=0.1, reorder_delay=0.01, seed=5)

    def test_corruption_is_detected_and_recovered(self) -> None:
        _, proxy, _ = self.transfer(200_000, corrupt=0.03, seed=6)
        self.assertGreater(proxy.stats["corrupted"], 0)

    def test_combined_impairments(self) -> None:
        self.transfer(
            200_000, drop=0.03, duplicate=0.05, reorder=0.05, corrupt=0.01,
            reorder_delay=0.01, seed=8,
        )

    def test_syn_loss(self) -> None:
        _, proxy, elapsed = self.transfer(20_000, hook=drop_first(FORWARD, FLAG_SYN))
        self.assertEqual(proxy.stats["dropped"], 1)
        self.assertGreaterEqual(elapsed, 0.9)  # one 1 s SYN retransmission timer

    def test_syn_ack_loss(self) -> None:
        _, proxy, _ = self.transfer(20_000, hook=drop_first(REVERSE, FLAG_SYN | FLAG_ACK))
        self.assertEqual(proxy.stats["dropped"], 1)

    def test_fin_loss(self) -> None:
        _, proxy, _ = self.transfer(20_000, hook=drop_first(FORWARD, FLAG_FIN, count=2))
        self.assertEqual(proxy.stats["dropped"], 2)

    def test_fin_ack_loss(self) -> None:
        _, proxy, _ = self.transfer(
            20_000, hook=drop_first(REVERSE, FLAG_FIN | FLAG_ACK, count=2)
        )
        self.assertEqual(proxy.stats["dropped"], 2)

    def test_empty_file_with_loss(self) -> None:
        self.transfer(payload=b"", drop=0.1, seed=9)

    def test_receiver_gives_up_when_sender_vanishes(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            connection = Connection(sock, idle_timeout=0.5)
            started = time.monotonic()
            with self.assertRaises(ConnectionError):
                connection.receive_bytes()
            self.assertLess(time.monotonic() - started, 3.0)

    def test_sender_gives_up_when_receiver_vanishes(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dead:
            dead.bind(("127.0.0.1", 0))  # bound but never answers
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                connection = Connection(sock, timeout=0.02)
                with self.assertRaises(ConnectionError):
                    connection.connect(dead.getsockname())


if __name__ == "__main__":
    unittest.main()
