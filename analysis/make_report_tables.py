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
    "Exp4": "Experiment 4 - RUDP and TCP Reno competing on a 10 Mbit/s bottleneck",
}


def number(value: str, digits: int = 2) -> str:
    try:
        parsed = float(value)
    except ValueError:
        return "-"
    return "-" if parsed != parsed else f"{parsed:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args()
    with (args.results / "summary.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for experiment in sorted({row["experiment"] for row in rows}):
        print(f"### {TITLES.get(experiment, experiment)}\n")
        print("| Condition | Protocol | OK/runs | Goodput (Mbit/s) mean +/- sd | "
              "Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |")
        print("|---|---|---|---|---|---|---|---|")
        for row in rows:
            if row["experiment"] != experiment:
                continue
            try:
                rtt = number(str(float(row["mean_rtt_estimate_s"]) * 1000), 1)
            except ValueError:
                rtt = "-"
            queue = number(row.get("mean_queue_delay_ms", ""), 1)
            print(
                f"| {row['condition']} | {row['protocol']} | {row['runs_ok']}/{row['runs']} | "
                f"{number(row['mean_goodput_mbps'])} +/- {number(row['stddev_goodput_mbps'])} | "
                f"{number(row['mean_completion_time_s'])} +/- {number(row['stddev_completion_time_s'])} | "
                f"{number(row['mean_retransmissions'], 1)} | {rtt} | {queue} |"
            )
        if experiment == "Exp4":
            shares = [(r["protocol"], r["mean_share_of_total"], r["mean_fairness_index"])
                      for r in rows if r["experiment"] == "Exp4" and r["mean_fairness_index"]]
            for protocol, share, jain in shares:
                print(f"\n{protocol}: share of throughput while both run {number(str(float(share) * 100), 1)}%, "
                      f"Jain fairness index {number(jain, 3)}")
        print()


if __name__ == "__main__":
    main()
