"""Selective Repeat sender tests."""

import time
import unittest

from rudp.sender import Sender


class SenderTests(unittest.TestCase):
    def test_window_limits_packets(self) -> None:
        sender = Sender(window_size=2)
        packets = sender.make_packets(b"abcd", chunk_size=1)
        self.assertEqual(len(packets), 2)
        self.assertFalse(sender.can_send())
        sender.acknowledge(2)
        self.assertTrue(sender.can_send())

    def test_retransmission_after_timeout(self) -> None:
        sender = Sender(window_size=1)
        packet = sender.make_packets(b"x")[0]
        sender.controller._timeout = 0.001  # test-only controller configuration
        time.sleep(0.002)
        self.assertEqual(sender.retransmissions(), [packet])


if __name__ == "__main__":
    unittest.main()
