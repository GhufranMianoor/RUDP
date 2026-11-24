"""Selective Repeat sender tests, including RTT sampling and controller integration."""

import time
import unittest

from rudp.controller import CongestionState, RenoController
from rudp.packet import SACKBlock
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
        retransmitted = sender.retransmissions()
        self.assertEqual([p.sequence for p in retransmitted], [packet.sequence])
        self.assertEqual(retransmitted[0].payload, packet.payload)

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

    def test_three_higher_acks_trigger_fast_retransmission(self) -> None:
        sender = Sender(window_size=4)
        packets = sender.make_packets(b"abcd", chunk_size=1)
        self.assertEqual(len(packets), 4)

        sender.acknowledge(
            1,
            (
                SACKBlock(2, 2),
                SACKBlock(3, 3),
                SACKBlock(4, 4),
            ),
        )

        self.assertEqual([p.sequence for p in sender.retransmissions()], [1])

    def test_fast_retransmit_does_not_collapse_cwnd_or_backoff_rto(self) -> None:
        reno = RenoController(initial_cwnd=10.0, initial_ssthresh=32.0)
        sender = Sender(controller=reno, window_size=10)
        packets = sender.make_packets(b"abcd", chunk_size=1)
        sender.acknowledge(
            1,
            (SACKBlock(2, 2), SACKBlock(3, 3), SACKBlock(4, 4)),
        )
        cwnd_after_fast_recovery = reno.cwnd
        rto_before = reno.rto()
        self.assertEqual([p.sequence for p in sender.retransmissions()], [1])
        self.assertEqual(reno.cwnd, cwnd_after_fast_recovery)
        self.assertEqual(reno.rto(), rto_before)

    def test_simultaneous_timeouts_apply_one_congestion_event(self) -> None:
        reno = RenoController(initial_cwnd=8.0, initial_ssthresh=32.0, timeout=0.001)
        sender = Sender(controller=reno, window_size=8)
        sender.make_packets(b"abcdefgh", chunk_size=1)
        time.sleep(0.003)
        sender.retransmissions()
        # One timeout halves ssthresh once (8 -> 4), not once per packet.
        self.assertEqual(reno.ssthresh, 4.0)
        self.assertEqual(reno.cwnd, 1.0)


if __name__ == "__main__":
    unittest.main()


class SackScoreboardTests(unittest.TestCase):
    """Loss detection, once-per-window reaction and retransmission pacing."""

    def test_many_holes_cause_a_single_window_reduction(self) -> None:
        reno = RenoController(initial_cwnd=20.0, initial_ssthresh=64.0)
        sender = Sender(controller=reno, window_size=64)
        sender.make_packets(bytes(20), chunk_size=1)
        # Packets 1, 3, 5 lost; 2, 4, 6..20 delivered.
        delivered = [2, 4] + list(range(6, 21))
        for sequence in delivered:
            sender.acknowledge(1, (SACKBlock(sequence, sequence),))
        self.assertEqual(reno.state, CongestionState.FAST_RECOVERY)
        # Reduced exactly once: ssthresh is half of the pre-loss window.
        self.assertAlmostEqual(reno.ssthresh, reno._cwnd, places=6)
        self.assertGreaterEqual(reno.ssthresh, 10.0)
        self.assertLess(reno.ssthresh, 20.0)
        before = reno.ssthresh
        sender.retransmissions()
        sender.acknowledge(1, (SACKBlock(20, 20),))
        self.assertEqual(reno.ssthresh, before)

    def test_recovery_ends_when_cumulative_ack_passes_loss_event(self) -> None:
        reno = RenoController(initial_cwnd=10.0, initial_ssthresh=64.0)
        sender = Sender(controller=reno, window_size=64)
        sender.make_packets(bytes(10), chunk_size=1)
        sender.acknowledge(1, (SACKBlock(2, 2),))
        sender.acknowledge(1, (SACKBlock(3, 3),))
        sender.acknowledge(1, (SACKBlock(4, 4),))
        self.assertEqual(reno.state, CongestionState.FAST_RECOVERY)
        self.assertEqual([p.sequence for p in sender.retransmissions()], [1])
        sender.acknowledge(11)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)

    def test_timeout_retransmits_only_what_the_window_allows(self) -> None:
        reno = RenoController(initial_cwnd=8.0, initial_ssthresh=32.0, timeout=0.001)
        sender = Sender(controller=reno, window_size=8)
        sender.make_packets(bytes(8), chunk_size=1)
        time.sleep(0.003)
        sent = sender.retransmissions()
        # cwnd collapsed to 1: one retransmission now, not a burst of eight.
        self.assertEqual([p.sequence for p in sent], [1])
        self.assertEqual(sender.unacked, 8)
        self.assertEqual(sender.in_flight, 1)
        # An ACK for it opens the window for the next lost packets.
        sender.acknowledge(2)
        self.assertEqual([p.sequence for p in sender.retransmissions()], [2, 3])

    def test_retransmission_has_fresh_timestamp(self) -> None:
        sender = Sender(window_size=1)
        original = sender.make_packets(b"x")[0]
        sender.controller._timeout = 0.001
        time.sleep(0.01)
        retransmitted = sender.retransmissions()[0]
        self.assertGreater(retransmitted.timestamp, original.timestamp)

    def test_lost_retransmission_is_detected_by_later_sacks(self) -> None:
        sender = Sender(window_size=16)
        sender.make_packets(bytes(8), chunk_size=1)
        for sequence in (2, 3, 4):
            sender.acknowledge(1, (SACKBlock(sequence, sequence),))
        self.assertEqual([p.sequence for p in sender.retransmissions()], [1])
        # Retransmission of 1 is lost. New data goes out and gets SACKed.
        sender.make_packets(bytes(3), start=0, chunk_size=1)
        for sequence in (9, 10, 11):
            sender.acknowledge(1, (SACKBlock(sequence, sequence),))
        self.assertIn(1, [p.sequence for p in sender.retransmissions()])

    def test_zero_window_allows_a_single_probe(self) -> None:
        sender = Sender(window_size=8)
        sender.set_remote_window(0)
        self.assertEqual(len(sender.make_packets(bytes(8), chunk_size=1)), 1)
        self.assertFalse(sender.can_send())
        sender.acknowledge(2, remote_window=4)
        self.assertTrue(sender.can_send())

    def test_make_packets_with_start_offset_does_not_copy_tail(self) -> None:
        sender = Sender(window_size=4)
        packets = sender.make_packets(b"abcdef", chunk_size=2, start=2)
        self.assertEqual([p.payload for p in packets], [b"cd", b"ef"])


class LossEpisodeRegressionTests(unittest.TestCase):
    """Regressions for congestion responses found in review (2026-11-24)."""

    def test_staggered_timeouts_in_one_episode_reduce_once(self) -> None:
        # Packets sent a few ms apart expire in different scans. Even when an
        # RTT sample resets the backed-off RTO in between, they belong to one
        # timeout episode and must cause a single congestion response.
        reno = RenoController(initial_cwnd=8.0, initial_ssthresh=32.0, timeout=0.005)
        sender = Sender(controller=reno, window_size=8)
        data = bytes(4)
        sender.make_packets(data[:2], chunk_size=1)
        time.sleep(0.003)
        sender.make_packets(data, chunk_size=1, start=2)
        time.sleep(0.003)  # first two expired, last two not yet
        sender.retransmissions()
        self.assertEqual(sender.stats["timeouts"], 1)
        self.assertEqual(reno.ssthresh, 4.0)
        reno._timeout = 0.005  # as if a fresh RTT sample erased the backoff
        time.sleep(0.004)
        sender.retransmissions()
        self.assertEqual(sender.stats["timeouts"], 1)
        self.assertEqual(reno.ssthresh, 4.0)

    def test_new_loss_event_during_sack_recovery_reduces_again(self) -> None:
        # A loss of data sent after recovery began is a new congestion event;
        # recovery must not swallow it and keep cwnd frozen.
        reno = RenoController(initial_cwnd=10.0, initial_ssthresh=64.0)
        sender = Sender(controller=reno, window_size=64)
        data = bytes(14)
        sender.make_packets(data[:10], chunk_size=1)
        sender.acknowledge(1, (SACKBlock(2, 4),))  # 1 lost -> recovery
        self.assertEqual(reno.state, CongestionState.FAST_RECOVERY)
        first = reno.ssthresh
        sender.retransmissions()
        sender.acknowledge(1, (SACKBlock(2, 10),))
        new = sender.make_packets(data, chunk_size=1, start=10)
        self.assertEqual([p.sequence for p in new], [11, 12, 13, 14])
        sender.acknowledge(1, (SACKBlock(12, 14), SACKBlock(2, 10)))  # 11 lost too
        self.assertLess(reno.ssthresh, first)
