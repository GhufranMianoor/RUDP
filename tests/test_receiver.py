"""Receiver reorder-buffer tests."""

import unittest

from rudp.constants import FLAG_DATA
from rudp.packet import Packet
from rudp.receiver import Receiver


class ReceiverTests(unittest.TestCase):
    def test_reorders_and_releases_contiguous_data(self) -> None:
        receiver = Receiver(initial_sequence=1)
        output, ack = receiver.receive(Packet(flags=FLAG_DATA, sequence=2, payload=b"b"))
        self.assertEqual(output, b"")
        self.assertEqual(ack.acknowledgement, 1)
        output, ack = receiver.receive(Packet(flags=FLAG_DATA, sequence=1, payload=b"a"))
        self.assertEqual(output, b"ab")
        self.assertEqual(ack.acknowledgement, 3)

    def test_duplicate_does_not_duplicate_output(self) -> None:
        receiver = Receiver()
        packet = Packet(flags=FLAG_DATA, sequence=1, payload=b"a")
        self.assertEqual(receiver.receive(packet)[0], b"a")
        self.assertEqual(receiver.receive(packet)[0], b"")


if __name__ == "__main__":
    unittest.main()
