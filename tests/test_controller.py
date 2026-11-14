"""Unit tests for congestion controller interface and Reno implementation (Member 2 - Week 1)."""

from __future__ import annotations

import tempfile
import unittest

from rudp.controller import (
    CongestionController,
    CongestionState,
    Controller,
    NullController,
    RenoController,
)
from rudp.eventlog import EventLogger


class ControllerInterfaceTests(unittest.TestCase):
    def test_null_controller_implements_interface(self) -> None:
        """NullController satisfies Controller and CongestionController protocols."""
        ctrl = NullController(window_size=16, timeout=0.1)
        self.assertIsInstance(ctrl, Controller)
        self.assertIsInstance(ctrl, CongestionController)
        self.assertEqual(ctrl.window(), 16)
        self.assertEqual(ctrl.rto(), 0.1)
        self.assertEqual(ctrl.cwnd, 16.0)
        self.assertEqual(ctrl.ssthresh, 16.0)
        self.assertEqual(ctrl.state, CongestionState.CONGESTION_AVOIDANCE)

        # Call methods to ensure no exceptions
        ctrl.on_packet_sent(1)
        ctrl.on_ack(1, 0.02)
        self.assertFalse(ctrl.on_duplicate_ack(1))
        ctrl.on_loss(1)
        ctrl.on_rto_timeout()

        stats = ctrl.stats()
        self.assertEqual(stats["type"], "NullController")
        self.assertEqual(stats["cwnd"], 16)

    def test_reno_controller_implements_interface(self) -> None:
        """RenoController satisfies Controller and CongestionController protocols."""
        reno = RenoController(initial_cwnd=1.0, initial_ssthresh=32.0, timeout=0.2)
        self.assertIsInstance(reno, Controller)
        self.assertIsInstance(reno, CongestionController)
        self.assertEqual(reno.window(), 1)
        self.assertEqual(reno.rto(), 0.2)
        self.assertEqual(reno.cwnd, 1.0)
        self.assertEqual(reno.ssthresh, 32.0)
        self.assertEqual(reno.state, CongestionState.SLOW_START)


class RenoControllerTests(unittest.TestCase):
    def test_slow_start_exponential_growth(self) -> None:
        """In Slow Start, each valid ACK increases cwnd by 1.0."""
        reno = RenoController(initial_cwnd=1.0, initial_ssthresh=4.0)
        self.assertEqual(reno.state, CongestionState.SLOW_START)

        # ACK 1 arrives -> cwnd becomes 2.0
        reno.on_ack(1, 0.02)
        self.assertEqual(reno.cwnd, 2.0)
        self.assertEqual(reno.window(), 2)
        self.assertEqual(reno.state, CongestionState.SLOW_START)

        # ACK 2 arrives -> cwnd becomes 3.0
        reno.on_ack(2, 0.02)
        self.assertEqual(reno.cwnd, 3.0)
        self.assertEqual(reno.state, CongestionState.SLOW_START)

        # ACK 3 arrives -> cwnd becomes 4.0 >= ssthresh -> transition to Congestion Avoidance
        reno.on_ack(3, 0.02)
        self.assertEqual(reno.cwnd, 4.0)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)

    def test_congestion_avoidance_linear_growth(self) -> None:
        """In Congestion Avoidance, cwnd increases by 1/cwnd per ACK (~1 packet per RTT)."""
        reno = RenoController(initial_cwnd=4.0, initial_ssthresh=4.0)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)

        # Receive 4 ACKs (a full window of 4 packets)
        for seq in range(1, 5):
            reno.on_ack(seq, 0.02)

        # Each ACK added 1/4 = 0.25 -> Total increase is 4 * 0.25 = 1.0 -> cwnd is 5.0
        self.assertAlmostEqual(reno.cwnd, 5.0, places=4)
        self.assertEqual(reno.window(), 5)

    def test_fast_retransmit_and_fast_recovery(self) -> None:
        """3 Duplicate ACKs trigger Fast Retransmit and enter Fast Recovery."""
        reno = RenoController(initial_cwnd=10.0, initial_ssthresh=32.0)
        # Advance into congestion avoidance
        reno._cwnd = 10.0
        reno._ssthresh = 8.0
        reno._state = CongestionState.CONGESTION_AVOIDANCE

        # 1st and 2nd duplicate ACKs
        self.assertFalse(reno.on_duplicate_ack(1))
        self.assertEqual(reno.dup_acks, 1)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)

        self.assertFalse(reno.on_duplicate_ack(1))
        self.assertEqual(reno.dup_acks, 2)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)

        # 3rd duplicate ACK triggers Fast Retransmit
        triggered = reno.on_duplicate_ack(1)
        self.assertTrue(triggered)
        self.assertEqual(reno.state, CongestionState.FAST_RECOVERY)
        # ssthresh = max(2, 10 / 2) = 5.0
        self.assertEqual(reno.ssthresh, 5.0)
        # cwnd = ssthresh + 3 = 8.0
        self.assertEqual(reno.cwnd, 8.0)

        # 4th duplicate ACK inflates window in Fast Recovery
        reno.on_duplicate_ack(1)
        self.assertEqual(reno.cwnd, 9.0)

        # Fresh ACK arrives acknowledging new data: deflates cwnd to ssthresh
        reno.on_ack(2, 0.02)
        self.assertEqual(reno.state, CongestionState.CONGESTION_AVOIDANCE)
        self.assertEqual(reno.cwnd, 5.0)
        self.assertEqual(reno.dup_acks, 0)

    def test_rto_timeout_loss(self) -> None:
        """RTO timeout halves ssthresh and resets cwnd to 1 in Slow Start."""
        reno = RenoController(initial_cwnd=16.0, initial_ssthresh=32.0)
        reno._cwnd = 16.0
        reno._ssthresh = 16.0
        reno._state = CongestionState.CONGESTION_AVOIDANCE

        reno.on_loss(5)
        # ssthresh = max(2, 16 / 2) = 8.0
        self.assertEqual(reno.ssthresh, 8.0)
        # cwnd resets to 1.0
        self.assertEqual(reno.cwnd, 1.0)
        self.assertEqual(reno.window(), 1)
        self.assertEqual(reno.state, CongestionState.SLOW_START)
        self.assertEqual(reno.dup_acks, 0)

    def test_event_logging_and_observer(self) -> None:
        """RenoController logs events to EventLogger and notifies observers."""
        events: list[dict] = []

        with tempfile.NamedTemporaryFile("w+", delete=False) as tmp:
            logger = EventLogger(destination=tmp.name)
            reno = RenoController(initial_cwnd=2.0, initial_ssthresh=4.0, logger=logger)
            reno.add_observer(lambda record: events.append(record))

            reno.on_ack(1, 0.01)
            reno.on_loss(1)
            logger.close()

        # Check observed events
        self.assertGreaterEqual(len(events), 2)
        triggers = [e["trigger"] for e in events]
        self.assertIn("slow_start_ack", triggers)
        self.assertIn("timeout_loss", triggers)


if __name__ == "__main__":
    unittest.main()

