#!/usr/bin/env python3
"""Turn results/summary.csv into Markdown tables to paste into REPORT.md.

    python analysis/make_report_tables.py --results results > results/report_tables.md

Only measured numbers are printed; nothing is estimated or filled in.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

TITLES = {
    "Exp1": "Experiment 1 - packet loss (RTT 100 ms, 10 Mbit/s; loss is per direction)",
    "Exp2": "Experiment 2 - round-trip time (no loss, 10 Mbit/s)",
    "Exp3": "Experiment 3 - bottleneck rate (RTT 100 ms, no loss)",
    "Exp4": "Experiment 4 - RUDP competing with TCP Reno and with TCP CUBIC on a 10 Mbit/s bottleneck (RTT 20, 100, 200 ms)",
}


def number(value: str, digits: int = 2) -> str:
    try:
        parsed = float(value)
    except ValueError:
        return "-"
    return "-" if parsed != parsed else f"{parsed:.{digits}f}"


def spread(row: dict[str, str], field: str, digits: int = 2, scale: float = 1.0) -> str:
    """``mean +/- sd`` of a summary.csv metric, or ``-`` when it was not measured."""
    try:
        mean = float(row.get(f"mean_{field}", "")) * scale
    except ValueError:
        return "-"
    try:
        sd = number(str(float(row.get(f"stddev_{field}", "")) * scale), digits)
    except ValueError:
        sd = "-"
    return f"{number(str(mean), digits)} +/- {sd}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args()
    with (args.results / "summary.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for experiment in sorted({row["experiment"] for row in rows}):
        print(f"### {TITLES.get(experiment, experiment)}\n")
        print("All values are mean +/- standard deviation over the successful runs.\n")
        print("| Condition | Protocol | OK/runs | Goodput (Mbit/s) | "
              "Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |")
        print("|---|---|---|---|---|---|---|---|")
        for row in rows:
            if row["experiment"] != experiment:
                continue
            print(
                f"| {row['condition']} | {row['protocol']} | {row['runs_ok']}/{row['runs']} | "
                f"{spread(row, 'goodput_mbps')} | "
                f"{spread(row, 'completion_time_s')} | "
                f"{spread(row, 'retransmissions', 1)} | "
                f"{spread(row, 'rtt_estimate_s', 1, 1000)} | "
                f"{spread(row, 'queue_delay_ms', 1)} |"
            )
        if experiment == "Exp2":
            print("\nRTT estimate versus measured RTT (ms; ping = 20 ICMP probes on the idle path "
                  "before each run):\n")
            print("| Condition | RUDP smoothed RTT | RUDP minimum RTT | ping average +/- sd | ping minimum |")
            print("|---|---|---|---|---|")
            for row in rows:
                if row["experiment"] != "Exp2" or not row["protocol"].startswith("RUDP"):
                    continue
                try:
                    srtt = number(str(float(row["mean_rtt_estimate_s"]) * 1000), 1)
                except ValueError:
                    srtt = "-"
                print(f"| {row['condition']} ({row['protocol']}) | {srtt} | "
                      f"{number(row.get('mean_min_rtt_ms', ''), 1)} | "
                      f"{number(row.get('mean_ping_avg_rtt_ms', ''), 1)} +/- "
                      f"{number(row.get('stddev_ping_avg_rtt_ms', ''), 1)} | "
                      f"{number(row.get('mean_ping_min_rtt_ms', ''), 1)} |")
        if experiment == "Exp4":
            print("\nFairness while both flows run (RUDP row of each condition; mean +/- sd over runs, "
                  "range in brackets):\n")
            print("| Condition | Competing TCP | RTT | OK/runs | RUDP share of throughput (%) | "
                  "Jain's fairness index |")
            print("|---|---|---|---|---|---|")
            for r in rows:
                if r["experiment"] != "Exp4" or not r["protocol"].startswith("RUDP"):
                    continue
                tcp = "CUBIC" if "cubic" in r["condition"] else "Reno"
                rtt = r["condition"].rsplit("_rtt", 1)[-1] if "_rtt" in r["condition"] else "-"

                def pct(key: str) -> str:
                    try:
                        return number(str(float(r.get(key, "")) * 100), 1)
                    except ValueError:
                        return "-"

                print(f"| {r['condition']} | {tcp} | {rtt} | {r['runs_ok']}/{r['runs']} | "
                      f"{pct('mean_share_of_total')} +/- {pct('stddev_share_of_total')} | "
                      f"{number(r.get('mean_fairness_index', ''), 3)} +/- "
                      f"{number(r.get('stddev_fairness_index', ''), 3)} "
                      f"[{number(r.get('min_fairness_index', ''), 3)}-"
                      f"{number(r.get('max_fairness_index', ''), 3)}] |")
        print()


if __name__ == "__main__":
    main()
