"""Tests for the pure helpers in topology/, experiments/, analysis/ and tools/ (no Mininet needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("topology", "experiments", "analysis", "."):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

import make_report_tables  # noqa: E402
import run_experiments  # noqa: E402
import topo  # noqa: E402


class TopologyMathTests(unittest.TestCase):
    def test_parse_rate(self) -> None:
        self.assertEqual(topo.parse_rate_bps("10mbit"), 10_000_000)
        self.assertEqual(topo.parse_rate_bps("500kbit"), 500_000)
        self.assertEqual(topo.parse_rate_bps("1.5mbit"), 1_500_000)
        with self.assertRaises(ValueError):
            topo.parse_rate_bps("10mbps")

    def test_tbf_burst_reaches_configured_rate_and_queue_is_100_packets(self) -> None:
        for rate in ("5mbit", "10mbit", "20mbit"):
            burst, limit = topo.tbf_params(rate, 100)
            rate_bytes = topo.parse_rate_bps(rate) // 8
            self.assertGreaterEqual(burst, topo.MTU)               # at least one packet
            self.assertGreaterEqual(burst, rate_bytes // 250)       # at least rate / HZ (HZ=250)
            self.assertEqual(limit, 100 * topo.MTU)
            self.assertGreater(limit, burst)                        # the old "limit 1000" bug

    def test_impairment_commands_build_netem_with_tbf_child(self) -> None:
        netem, tbf = topo.impairment_commands("R1-eth1", "50ms", "2%", "10mbit", 100)
        self.assertIn("dev R1-eth1 root handle 1: netem delay 50ms loss 2%", netem)
        self.assertIn("dev R1-eth1 parent 1:1 handle 10: tbf rate 10mbit", tbf)
        self.assertIn("limit 150000", tbf)
        self.assertNotIn("latency", tbf)  # latency and limit conflict

    def test_parsers(self) -> None:
        trace = " 1  10.0.1.1  0.4 ms\n 2  10.0.2.2  0.4 ms\n 3  10.0.3.10  0.6 ms\n 4  * * *\n"
        self.assertEqual(topo._hops(trace), ["10.0.1.1", "10.0.2.2", "10.0.3.10"])
        ping = "20 packets transmitted, 19 received, 5% packet loss, time 3ms\nrtt min/avg/max/mdev = 1.0/20.5/30.0/2.0 ms"
        self.assertEqual(topo._ping_stats(ping), (5.0, 20.5))
        self.assertEqual(topo._duration_ms("25ms"), 25.0)
        self.assertEqual(topo._duration_ms("0.1s"), 100.0)


class RunnerTests(unittest.TestCase):
    def test_matrix_has_three_conditions_per_metric_and_concurrent_fairness(self) -> None:
        by_experiment: dict[str, list] = {}
        for condition in run_experiments.conditions():
            by_experiment.setdefault(condition.experiment, []).append(condition)
        self.assertEqual({k: len(v) for k, v in by_experiment.items()},
                         {"Exp1": 3, "Exp2": 3, "Exp3": 3, "Exp4": 1})
        self.assertTrue(by_experiment["Exp4"][0].concurrent)
        self.assertFalse(any(c.concurrent for e in ("Exp1", "Exp2", "Exp3") for c in by_experiment[e]))

    def test_rtt_is_twice_the_one_way_delay(self) -> None:
        rtts = {c.name: c.rtt_ms for c in run_experiments.conditions() if c.experiment == "Exp2"}
        self.assertEqual(rtts, {"rtt_20ms": 20, "rtt_100ms": 100, "rtt_200ms": 200})

    def test_jains_fairness(self) -> None:
        self.assertAlmostEqual(run_experiments.jains_fairness([5, 5]), 1.0)
        self.assertAlmostEqual(run_experiments.jains_fairness([10, 0]), 0.5)
        self.assertEqual(run_experiments.jains_fairness([]), 0.0)

    def test_payload_is_deterministic_and_not_a_repeating_pattern(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.bin"
            first = run_experiments.ensure_payload(path, 65536)
            second = run_experiments.ensure_payload(path, 65536)
            self.assertEqual(first, second)
            data = path.read_bytes()
            self.assertNotEqual(data[:256] * 256, data)

    def test_list_mode_needs_no_network(self) -> None:
        import subprocess
        out = subprocess.run(
            [sys.executable, str(ROOT / "experiments/run_experiments.py"), "--list", "--only", "Exp4"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("concurrently", out)


class ReportTableTests(unittest.TestCase):
    def test_number_formatting_handles_blank_and_nan(self) -> None:
        self.assertEqual(make_report_tables.number("9.4777"), "9.48")
        self.assertEqual(make_report_tables.number(""), "-")
        self.assertEqual(make_report_tables.number("nan"), "-")


class DissectorRegressionTests(unittest.TestCase):
    def test_dissector_does_not_reuse_the_builtin_rudp_name(self) -> None:
        # Wireshark ships a "Reliable UDP" dissector; reusing its name or
        # description stops this plugin from loading at all.
        text = (ROOT / "tools/rudp_dissector.lua").read_text()
        self.assertIn('Proto("rudpcn", "CN Project Reliable UDP")', text)
        self.assertNotIn('Proto("rudp",', text)
        for name in ("SYN", "ACK", "FIN", "DATA", "RST"):
            self.assertIn(f'"{name}"', text)
        self.assertIn("inclusive", text)


class LossyProxyCliTests(unittest.TestCase):
    def test_proxy_script_has_a_main_guard(self) -> None:
        # The original script never called main() and exited immediately.
        text = (ROOT / "tools/lossy_proxy.py").read_text()
        self.assertIn('if __name__ == "__main__":', text)
        self.assertIn("main()", text.split('if __name__ == "__main__":')[1])


if __name__ == "__main__":
    unittest.main()
