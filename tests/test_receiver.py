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

    def test_sack_ranges_are_capped_at_wire_limit_most_recent_first(self) -> None:
        receiver = Receiver(window_size=16)
        for sequence in (2, 4, 6, 8, 10):
            receiver.receive(Packet(flags=FLAG_DATA, sequence=sequence, payload=b"x"))
        ack = receiver.ack()
        self.assertEqual(len(ack.sack_blocks), 3)
        # RFC 2018: newest out-of-order data first.
        self.assertEqual(
            [(block.start, block.end) for block in ack.sack_blocks],
            [(10, 10), (8, 8), (6, 6)],
        )

    def test_many_gaps_do_not_raise(self) -> None:
        receiver = Receiver(window_size=64)
        for sequence in range(2, 40, 2):  # 19 separate gaps
            _, ack = receiver.receive(Packet(flags=FLAG_DATA, sequence=sequence, payload=b"x"))
            self.assertLessEqual(len(ack.sack_blocks), 3)
            self.assertEqual(ack.acknowledgement, 1)

    def test_adjacent_packets_merge_into_one_inclusive_block(self) -> None:
        receiver = Receiver(window_size=16)
        for sequence in (3, 5, 4):
            _, ack = receiver.receive(Packet(flags=FLAG_DATA, sequence=sequence, payload=b"x"))
        self.assertEqual([(b.start, b.end) for b in ack.sack_blocks], [(3, 5)])


if __name__ == "__main__":
    unittest.main()
