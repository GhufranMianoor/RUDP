"""Selective Repeat sender tests, including RTT sampling and controller integration."""

import time
import unittest

from rudp.controller import CongestionState, RenoController
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

    def test_sender_rtt_sampling_clean(self) -> None:
        """Sender correctly tracks send time and generates RTT samples on ACK."""
        sender = Sender(window_size=4)
        packets = sender.make_packets(b"hello world", chunk_size=5)
        self.assertEqual(len(packets), 3)

        # ACK packet 1
        time.sleep(0.002)
        sender.acknowledge(2)  # Acknowledges sequence 1
        self.assertEqual(sender.rtt_sampler.stats.sample_count, 1)
        self.assertGreater(sender.rtt_sampler.stats.latest_rtt, 0.0)

    def test_sender_karns_algorithm_retransmission(self) -> None:
        """Karn's algorithm: Sender ignores RTT for retransmitted packets."""
        sender = Sender(window_size=2)
        packet = sender.make_packets(b"xyz", chunk_size=3)[0]
        sender.controller._timeout = 0.001
        time.sleep(0.002)

        # Trigger retransmission
        retrans = sender.retransmissions()
        self.assertEqual(len(retrans), 1)
        self.assertTrue(sender.rtt_sampler.is_retransmitted(packet.sequence))

        # ACK arrives after retransmission
        sender.acknowledge(packet.sequence + 1)
        self.assertEqual(sender.rtt_sampler.stats.sample_count, 0)
        self.assertEqual(sender.rtt_sampler.stats.ambiguous_count, 1)

    def test_sender_with_reno_controller(self) -> None:
        """Sender works seamlessly with RenoController."""
        reno = RenoController(initial_cwnd=1.0, initial_ssthresh=4.0)
        sender = Sender(controller=reno)

        # cwnd=1 -> can send 1 packet
        packets = sender.make_packets(b"12345678", chunk_size=2)
        self.assertEqual(len(packets), 1)
        self.assertFalse(sender.can_send())

        # ACK sequence 1 -> Reno slow start increases cwnd to 2
        sender.acknowledge(2)
        self.assertEqual(reno.cwnd, 2.0)
        self.assertTrue(sender.can_send())

        # Sender can now send 2 more packets
        more = sender.make_packets(b"345678", chunk_size=2)
        self.assertEqual(len(more), 2)
        self.assertEqual(sender.in_flight, 2)

    def test_duplicate_ack_notification(self) -> None:
        """Duplicate ACK (acknowledgement <= base) increments Reno dup_acks."""
        reno = RenoController(initial_cwnd=4.0, initial_ssthresh=16.0)
        sender = Sender(controller=reno)
        packets = sender.make_packets(b"abcdef", chunk_size=2)
        self.assertEqual(len(packets), 3)

        # Send duplicate ACKs for sequence 1 (acknowledgement = 1 when base = 1)
        sender.acknowledge(1)
        self.assertEqual(reno.dup_acks, 1)
        sender.acknowledge(1)
        self.assertEqual(reno.dup_acks, 2)
        sender.acknowledge(1)
        self.assertEqual(reno.dup_acks, 3)
        self.assertEqual(reno.state, CongestionState.FAST_RECOVERY)


if __name__ == "__main__":
    unittest.main()
