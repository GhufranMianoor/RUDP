#!/usr/bin/env python3
"""Generate PNG plots from experiments/run_experiments.py output."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean, stdev


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"results file not found: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def grouped(rows: list[dict[str, str]], x_field: str, experiment: str) -> dict[str, dict[str, list[float]]]:
    result: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        if row["experiment"] != experiment:
            continue
        protocol = row["protocol"]
        x_value = row[x_field]
        result.setdefault(protocol, {}).setdefault(x_value, []).append(float(row["goodput_mbps"]))
    return result


def plot_goodput(rows: list[dict[str, str]], output: Path, x_field: str, experiment: str, title: str, name: str) -> None:
    import matplotlib.pyplot as plt

    data = grouped(rows, x_field, experiment)
    figure, axis = plt.subplots()
    for protocol, values in sorted(data.items()):
        x_values = sorted(values, key=float)
        y_values = [mean(values[x]) for x in x_values]
        errors = [stdev(values[x]) if len(values[x]) > 1 else 0 for x in x_values]
        axis.errorbar(x_values, y_values, yerr=errors, marker="o", capsize=3, label=protocol)
    axis.set(title=title, xlabel=x_field, ylabel="Goodput (Mbps)")
    axis.grid(alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output / name, dpi=160)
    plt.close(figure)


def plot_cwnd(logs: Path, output: Path) -> None:
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots()
    for path in sorted(logs.glob("*.jsonl")):
        times: list[float] = []
        cwnd: list[float] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "congestion_event" and "cwnd" in record:
                times.append(float(record.get("monotonic", record.get("time", 0))))
                cwnd.append(float(record["cwnd"]))
        if times:
            axis.plot(times, cwnd, label=path.stem)
    axis.set(title="Congestion window over time", xlabel="Monotonic time (s)", ylabel="cwnd (packets)")
    axis.grid(alpha=0.3)
    if axis.lines:
        axis.legend(fontsize="small")
    figure.tight_layout()
    figure.savefig(output / "cwnd_over_time.png", dpi=160)
    plt.close(figure)


def plot_rtt(rows: list[dict[str, str]], output: Path) -> None:
    import matplotlib.pyplot as plt

    points = [(float(row["delay_ms"]) * 2, float(row["rtt_estimate_s"]) * 1000)
              for row in rows if row.get("rtt_estimate_s")]
    figure, axis = plt.subplots()
    if points:
        x_values, y_values = zip(*points)
        axis.plot(x_values, y_values, "o-")
    axis.set(title="RTT estimate versus measured RTT", xlabel="Measured RTT (ms)", ylabel="Estimated RTT (ms)")
    axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(output / "rtt_estimate_vs_measured.png", dpi=160)
    plt.close(figure)


def plot_fairness(rows: list[dict[str, str]], output: Path) -> None:
    import matplotlib.pyplot as plt

    values = [float(row["fairness_index"]) for row in rows if row.get("fairness_index")]
    figure, axis = plt.subplots()
    axis.bar(["RUDP + TCP"], [mean(values) if values else 0.0])
    axis.set_ylim(0, 1)
    axis.set(title="Jain fairness index", ylabel="Fairness")
    figure.tight_layout()
    figure.savefig(output / "fairness.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--output", type=Path, default=Path("results/plots"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.results / "raw.csv")
    plot_goodput(rows, args.output, "loss_pct", "Exp1", "Goodput versus loss", "goodput_vs_loss.png")
    plot_goodput(rows, args.output, "delay_ms", "Exp2", "Goodput versus RTT", "goodput_vs_rtt.png")
    plot_goodput(rows, args.output, "rate_mbps", "Exp3", "Goodput versus bottleneck rate", "goodput_vs_rate.png")
    plot_cwnd(args.results / "logs", args.output)
    plot_rtt(rows, args.output)
    plot_fairness(rows, args.output)


if __name__ == "__main__":
    main()
