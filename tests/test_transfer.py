"""End-to-end transfer test."""

import socket
import threading
import unittest

from rudp.connection import Connection


class TransferTests(unittest.TestCase):
    def test_file_bytes_survive_transfer(self) -> None:
        payload = bytes(range(256)) * 8
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
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [payload])


if __name__ == "__main__":
    unittest.main()
