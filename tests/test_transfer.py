"""End-to-end transfer test."""

import socket
import threading
import unittest
import hashlib

from rudp.connection import Connection


class TransferTests(unittest.TestCase):
    def _transfer(self, payload: bytes) -> bytes:
        port: list[int] = []
        result: list[bytes] = []

        def receive() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port.append(sock.getsockname()[1])
                result.append(Connection(sock).receive_bytes())

        thread = threading.Thread(target=receive)
        thread.start()
        while not port:
            pass
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            connection = Connection(sock)
            connection.connect(("127.0.0.1", port[0]))
            connection.send_bytes(payload)
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        return result[0]

    def test_file_bytes_survive_transfer(self) -> None:
        payload = bytes(range(256)) * 8
        self.assertEqual(self._transfer(payload), payload)

    def test_empty_file_transfer(self) -> None:
        self.assertEqual(self._transfer(b""), b"")

    def test_ten_megabyte_transfer_sha256(self) -> None:
        payload = bytes(range(256)) * (10 * 1024 * 1024 // 256)
        received = self._transfer(payload)
        self.assertEqual(hashlib.sha256(received).digest(), hashlib.sha256(payload).digest())


if __name__ == "__main__":
    unittest.main()
