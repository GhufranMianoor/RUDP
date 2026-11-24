"""Unit tests for RTT sampling and Karn's algorithm (Member 2 - Week 1)."""

from __future__ import annotations

import unittest

from rudp.rtt import RTTSample, RTTSampler, RTTStats


class RTTSamplerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sampler = RTTSampler()

    def test_single_clean_sample(self) -> None:
        """First transmission acknowledged yields a valid RTT sample."""
        self.sampler.on_packet_sent(sequence=1, timestamp=10.0)
        self.assertTrue(self.sampler.is_tracked(1))
        self.assertFalse(self.sampler.is_retransmitted(1))

        rtt = self.sampler.on_packet_acked(sequence=1, timestamp=10.05)
        self.assertIsNotNone(rtt)
        self.assertAlmostEqual(rtt, 0.05, places=5)

        stats = self.sampler.stats
        self.assertEqual(stats.sample_count, 1)
        self.assertEqual(stats.ambiguous_count, 0)
        self.assertAlmostEqual(stats.latest_rtt, 0.05, places=5)
        self.assertAlmostEqual(stats.min_rtt, 0.05, places=5)
        self.assertAlmostEqual(stats.max_rtt, 0.05, places=5)
        self.assertAlmostEqual(stats.mean_rtt, 0.05, places=5)
        self.assertFalse(self.sampler.is_tracked(1))

    def test_karns_algorithm_explicit_retransmission(self) -> None:
        """RFC 6298: Retransmitted packets must NOT produce an RTT sample."""
        self.sampler.on_packet_sent(sequence=1, timestamp=1.0)
        self.sampler.on_packet_retransmitted(sequence=1, timestamp=1.5)
        self.assertTrue(self.sampler.is_retransmitted(1))

        # ACK arrives later
        rtt = self.sampler.on_packet_acked(sequence=1, timestamp=1.6)
        self.assertIsNone(rtt, "Karn's algorithm must return None for retransmitted packet")

        stats = self.sampler.stats
        self.assertEqual(stats.sample_count, 0)
        self.assertEqual(stats.ambiguous_count, 1)
        self.assertIsNone(stats.latest_rtt)

    def test_karns_algorithm_implicit_retransmission(self) -> None:
        """Calling on_packet_sent on an already-tracked packet flags it as retransmitted."""
        self.sampler.on_packet_sent(sequence=2, timestamp=2.0)
        self.assertFalse(self.sampler.is_retransmitted(2))

        # Re-sending sequence 2 without calling on_packet_retransmitted
        self.sampler.on_packet_sent(sequence=2, timestamp=2.3)
        self.assertTrue(self.sampler.is_retransmitted(2))

        rtt = self.sampler.on_packet_acked(sequence=2, timestamp=2.4)
        self.assertIsNone(rtt)
        self.assertEqual(self.sampler.stats.ambiguous_count, 1)
        self.assertEqual(self.sampler.stats.sample_count, 0)

    def test_untracked_sequence_ignored(self) -> None:
        """Acknowledging a sequence that was never tracked returns None."""
        rtt = self.sampler.on_packet_acked(sequence=99, timestamp=5.0)
        self.assertIsNone(rtt)
        self.assertEqual(self.sampler.stats.sample_count, 0)
        self.assertEqual(self.sampler.stats.ambiguous_count, 0)

    def test_multiple_packets_out_of_order(self) -> None:
        """Multiple packets tracked simultaneously with out-of-order ACKs."""
        self.sampler.on_packet_sent(sequence=1, timestamp=10.0)
        self.sampler.on_packet_sent(sequence=2, timestamp=10.1)
        self.sampler.on_packet_sent(sequence=3, timestamp=10.2)
        self.assertEqual(self.sampler.in_flight_count, 3)

        # ACK for 2 arrives first: RTT = 10.25 - 10.1 = 0.15
        rtt2 = self.sampler.on_packet_acked(sequence=2, timestamp=10.25)
        self.assertAlmostEqual(rtt2, 0.15, places=5)
        self.assertEqual(self.sampler.in_flight_count, 2)

        # Packet 1 was retransmitted
        self.sampler.on_packet_retransmitted(sequence=1, timestamp=10.3)
        rtt1 = self.sampler.on_packet_acked(sequence=1, timestamp=10.4)
        self.assertIsNone(rtt1)

        # ACK for 3 arrives: RTT = 10.5 - 10.2 = 0.30
        rtt3 = self.sampler.on_packet_acked(sequence=3, timestamp=10.5)
        self.assertAlmostEqual(rtt3, 0.30, places=5)

        stats = self.sampler.stats
        self.assertEqual(stats.sample_count, 2)
        self.assertEqual(stats.ambiguous_count, 1)
        self.assertAlmostEqual(stats.min_rtt, 0.15, places=5)
        self.assertAlmostEqual(stats.max_rtt, 0.30, places=5)
        self.assertAlmostEqual(stats.mean_rtt, 0.225, places=5)

    def test_observer_callback_and_history(self) -> None:
        """Observers receive notifications and history stores samples."""
        observed: list[tuple[RTTSample, RTTStats]] = []

        def callback(sample: RTTSample, stats: RTTStats) -> None:
            observed.append((sample, stats))

        self.sampler.add_observer(callback)

        self.sampler.on_packet_sent(sequence=1, timestamp=1.0)
        self.sampler.on_packet_acked(sequence=1, timestamp=1.05)

        self.assertEqual(len(observed), 1)
        sample, stats = observed[0]
        self.assertEqual(sample.sequence, 1)
        self.assertAlmostEqual(sample.rtt, 0.05, places=5)
        self.assertEqual(stats.sample_count, 1)

        history = self.sampler.history
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0], sample)

        # Remove observer and ensure it stops receiving events
        self.sampler.remove_observer(callback)
        self.sampler.on_packet_sent(sequence=2, timestamp=2.0)
        self.sampler.on_packet_acked(sequence=2, timestamp=2.08)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(self.sampler.history), 2)

    def test_clear_resets_state(self) -> None:
        """Clear resets all tracking data and stats."""
        self.sampler.on_packet_sent(sequence=1, timestamp=1.0)
        self.sampler.on_packet_acked(sequence=1, timestamp=1.05)
        self.sampler.clear()

        self.assertEqual(self.sampler.in_flight_count, 0)
        self.assertEqual(self.sampler.stats.sample_count, 0)
        self.assertEqual(len(self.sampler.history), 0)


if __name__ == "__main__":
    unittest.main()



class RtoMarginTests(unittest.TestCase):
    """RTTVAR decays on a stable path; RTO must still keep a margin above SRTT."""

    def test_rto_keeps_variance_floor_after_many_identical_samples(self):
        from rudp.constants import RTO_VARIANCE_FLOOR
        from rudp.controller import RenoController

        controller = RenoController()
        for _ in range(2000):
            controller.observe_rtt(0.200)
        self.assertGreaterEqual(controller.rto(), 0.200 + RTO_VARIANCE_FLOOR - 1e-9)

        sampler = RTTSampler()
        for sequence in range(2000):
            sampler.on_packet_sent(sequence, float(sequence))
            sampler.on_packet_acked(sequence, float(sequence) + 0.200)
        self.assertGreaterEqual(sampler.stats.rto, 0.200 + RTO_VARIANCE_FLOOR - 1e-9)
