"""Unit tests for the CUBIC congestion controller (RFC 8312 / RFC 9438)."""

from __future__ import annotations

import unittest

from rudp.controller import (
    CongestionController,
    CongestionState,
    CubicController,
    RenoController,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def cubic(**kwargs) -> tuple[CubicController, FakeClock]:
    clock = FakeClock()
    return CubicController(clock=clock, **kwargs), clock


def run_rtts(controller: CubicController, clock: FakeClock, rtts: int, rtt: float = 0.1) -> list[float]:
    """Deliver one window of ACKs per RTT; return cwnd at the end of each RTT."""
    sequence = 1000
    trace = []
    for _ in range(rtts):
        acks = controller.window()
        for i in range(acks):
            clock.now += rtt / acks
            sequence += 1
            controller.on_ack(sequence, rtt)
        trace.append(controller.cwnd)
    return trace


class CubicControllerTests(unittest.TestCase):
    def test_implements_interface(self) -> None:
        controller, _ = cubic()
        self.assertIsInstance(controller, CongestionController)
        self.assertIsInstance(controller, RenoController)
        stats = controller.stats()
        self.assertEqual(stats["type"], "CubicController")
        self.assertIn("w_max", stats)

    def test_slow_start_is_unchanged(self) -> None:
        controller, _ = cubic(initial_cwnd=1.0, initial_ssthresh=8.0)
        for seq in range(1, 4):
            controller.on_ack(seq, 0.1)
        self.assertEqual(controller.cwnd, 4.0)
        self.assertEqual(controller.state, CongestionState.SLOW_START)

    def test_loss_reduces_by_beta_and_remembers_w_max(self) -> None:
        controller, _ = cubic(initial_cwnd=100.0, initial_ssthresh=50.0)
        controller.on_fast_retransmit(10)
        self.assertAlmostEqual(controller.ssthresh, 70.0)   # beta = 0.7, Reno would give 50
        self.assertAlmostEqual(controller.cwnd, 70.0)
        self.assertAlmostEqual(controller.w_max, 100.0)
        self.assertEqual(controller.state, CongestionState.FAST_RECOVERY)

    def test_fast_convergence_lowers_w_max(self) -> None:
        controller, _ = cubic(initial_cwnd=100.0, initial_ssthresh=50.0)
        controller.on_fast_retransmit(10)
        controller.on_recovery_complete()
        # A second loss before reaching the old W_max: release bandwidth.
        controller.on_fast_retransmit(20)
        self.assertAlmostEqual(controller.w_max, 70.0 * (1 + 0.7) / 2)
        self.assertAlmostEqual(controller.ssthresh, 49.0)

    def test_fast_convergence_can_be_disabled(self) -> None:
        controller, _ = cubic(initial_cwnd=100.0, initial_ssthresh=50.0, fast_convergence=False)
        controller.on_fast_retransmit(10)
        controller.on_recovery_complete()
        controller.on_fast_retransmit(20)
        self.assertAlmostEqual(controller.w_max, 70.0)

    def test_rto_collapses_window_and_sets_ssthresh_by_beta(self) -> None:
        controller, _ = cubic(initial_cwnd=40.0, initial_ssthresh=20.0, timeout=0.5)
        controller.on_rto_timeout()
        self.assertEqual(controller.cwnd, 1.0)
        self.assertAlmostEqual(controller.ssthresh, 28.0)
        self.assertAlmostEqual(controller.w_max, 40.0)
        self.assertEqual(controller.state, CongestionState.SLOW_START)
        self.assertEqual(controller.rto(), 1.0)

    def test_window_returns_to_w_max_after_k_seconds(self) -> None:
        controller, clock = cubic(initial_cwnd=100.0, initial_ssthresh=50.0, tcp_friendly=False)
        controller.on_fast_retransmit(10)
        controller.on_recovery_complete()
        k = ((100.0 - 70.0) / 0.4) ** (1 / 3)               # about 4.2 s
        rtts = int(round(k / 0.1))
        trace = run_rtts(controller, clock, rtts)
        self.assertAlmostEqual(controller.k, k, places=6)
        self.assertGreater(trace[-1], 97.0)
        self.assertLess(trace[-1], 103.0)
        # Concave approach: the first half of the epoch grows faster than the second.
        half = rtts // 2
        self.assertGreater(trace[half] - 70.0, trace[-1] - trace[half])

    def test_growth_is_convex_beyond_w_max(self) -> None:
        controller, clock = cubic(initial_cwnd=100.0, initial_ssthresh=50.0, tcp_friendly=False)
        controller.on_fast_retransmit(10)
        controller.on_recovery_complete()
        trace = run_rtts(controller, clock, 80)               # 8 s, well past K
        steps = [b - a for a, b in zip(trace[50:], trace[51:])]
        self.assertGreater(trace[-1], 100.0)
        self.assertGreater(steps[-1], steps[0])               # accelerating again
        for step in steps:
            self.assertGreaterEqual(step, 0.0)

    def test_tcp_friendly_region_is_never_slower_than_reno_like_growth(self) -> None:
        # Small window, short RTT: the cubic curve is nearly flat, W_est dominates.
        friendly, clock_a = cubic(initial_cwnd=10.0, initial_ssthresh=5.0)
        pure, clock_b = cubic(initial_cwnd=10.0, initial_ssthresh=5.0, tcp_friendly=False)
        for controller in (friendly, pure):
            controller.on_fast_retransmit(1)
            controller.on_recovery_complete()
        a = run_rtts(friendly, clock_a, 20, rtt=0.01)
        b = run_rtts(pure, clock_b, 20, rtt=0.01)
        self.assertGreater(a[-1], b[-1])
        # 3 * (1 - 0.7) / (1 + 0.7) ~= 0.53 packets per RTT.
        self.assertAlmostEqual(a[-1] - 7.0, 20 * 0.53, delta=2.0)

    def test_cubic_recovers_faster_than_reno_on_a_long_rtt_path(self) -> None:
        reno = RenoController(initial_cwnd=200.0, initial_ssthresh=100.0)
        controller, clock = cubic(initial_cwnd=200.0, initial_ssthresh=100.0)
        for c in (reno, controller):
            c.on_fast_retransmit(1)
            c.on_recovery_complete()
        run_rtts(controller, clock, 50, rtt=0.2)               # 10 s at 200 ms RTT
        sequence = 0
        for _ in range(50):
            for _ in range(reno.window()):
                sequence += 1
                reno.on_ack(sequence, 0.2)
        self.assertGreater(controller.cwnd, reno.cwnd)

    def test_cwnd_never_shrinks_on_an_ack(self) -> None:
        controller, clock = cubic(initial_cwnd=50.0, initial_ssthresh=25.0)
        controller.on_fast_retransmit(1)
        controller.on_recovery_complete()
        previous = controller.cwnd
        for seq in range(2, 2000):
            clock.now += 0.001
            controller.on_ack(seq, 0.05)
            self.assertGreaterEqual(controller.cwnd, previous)
            previous = controller.cwnd

    def test_reno_is_unchanged_by_the_refactor(self) -> None:
        reno = RenoController(initial_cwnd=10.0, initial_ssthresh=8.0)
        reno.on_ack(1, 0.1)
        self.assertAlmostEqual(reno.cwnd, 10.1)
        reno.on_fast_retransmit(2)
        self.assertAlmostEqual(reno.ssthresh, 5.05)


if __name__ == "__main__":
    unittest.main()
