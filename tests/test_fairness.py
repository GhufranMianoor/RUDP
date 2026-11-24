"""Overlap-window fairness used by Exp4."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))

from fairness import (  # noqa: E402
    iperf_delivery_curve,
    jains_fairness,
    overlap_throughputs,
    rudp_delivery_curve,
)


class FairnessTests(unittest.TestCase):
    def test_flow_that_finishes_early_is_not_credited_with_the_idle_link(self) -> None:
        # RUDP: 5 Mbit/s for 10 s (625 kB/s = ~521 packets/s), starting at t=100.
        with tempfile.TemporaryDirectory() as work:
            log = Path(work) / "rudp.jsonl"
            with log.open("w") as stream:
                for i in range(5208):
                    stream.write(json.dumps({"event": "ack", "monotonic": 100 + i / 520.8}) + "\n")
                stream.write(json.dumps({"event": "send", "monotonic": 120}) + "\n")
            rudp = rudp_delivery_curve(log)
        # TCP: 5 Mbit/s for 10 s, then 10 Mbit/s alone for 5 s (same bytes as 20 s of 5).
        intervals = [{"sum": {"end": (k + 1) * 0.5, "bytes": 312_500}} for k in range(20)]
        intervals += [{"sum": {"end": 10 + (k + 1) * 0.5, "bytes": 625_000}} for k in range(10)]
        tcp = iperf_delivery_curve({"intervals": intervals}, started=100.0)
        a, b, window = overlap_throughputs(rudp, tcp)
        self.assertAlmostEqual(window, 10.0, delta=0.05)
        self.assertAlmostEqual(a, 5.0, delta=0.1)
        self.assertAlmostEqual(b, 5.0, delta=0.1)
        self.assertAlmostEqual(jains_fairness([a, b]), 1.0, places=3)

    def test_unequal_share_gives_lower_index(self) -> None:
        a = [(0.0, 0), (10.0, 10_000_000)]    # 8 Mbit/s
        b = [(0.0, 0), (10.0, 2_500_000)]     # 2 Mbit/s
        ra, rb, _ = overlap_throughputs(a, b)
        self.assertAlmostEqual(ra, 8.0)
        self.assertAlmostEqual(rb, 2.0)
        self.assertAlmostEqual(jains_fairness([ra, rb]), 100 / (2 * 68), places=6)

    def test_no_overlap(self) -> None:
        self.assertIsNone(overlap_throughputs([(0, 0), (1, 10)], [(2, 0), (3, 10)]))


if __name__ == "__main__":
    unittest.main()
