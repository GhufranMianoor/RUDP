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
        if row["experiment"] != experiment or row.get("ok") not in ("True", "true", "1"):
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


def _rudp_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def plot_cwnd(logs: Path, output: Path) -> None:
    """One cwnd/ssthresh-vs-time figure per RUDP event log (sawtooth under loss)."""

    import matplotlib.pyplot as plt

    for path in sorted(logs.glob("*_rudp.jsonl")):
        events = _rudp_events(path)
        points = [e for e in events if e.get("event") == "congestion_event" and "cwnd" in e]
        if not points:
            continue
        t0 = float(points[0].get("monotonic", 0))
        times = [float(e["monotonic"]) - t0 for e in points]
        figure, axis = plt.subplots()
        axis.plot(times, [e["cwnd"] for e in points], label="cwnd", linewidth=1)
        axis.plot(times, [e["ssthresh"] for e in points], "--", label="ssthresh", linewidth=1)
        losses = [float(e["monotonic"]) - t0 for e in events if e.get("event") == "timeout"]
        axis.plot(losses, [0] * len(losses), "rx", label="timeout")
        axis.set(title=f"Congestion window: {path.stem.replace('_rudp', '')}",
                 xlabel="Time since first ACK (s)", ylabel="packets")
        axis.grid(alpha=0.3)
        axis.legend()
        figure.tight_layout()
        figure.savefig(output / f"cwnd_{path.stem.replace('_rudp', '')}.png", dpi=160)
        plt.close(figure)


def plot_rtt(rows: list[dict[str, str]], output: Path) -> None:
    """RUDP smoothed RTT and minimum RTT against the configured RTT (Exp2)."""

    import matplotlib.pyplot as plt

    points = sorted(
        {(float(r["rtt_ms"]), float(r["rtt_estimate_s"]) * 1000,
          float(r["min_rtt_s"]) * 1000 if r.get("min_rtt_s") else float("nan"))
         for r in rows
         if r["experiment"] == "Exp2" and r["protocol"] == "RUDP"
         and r.get("ok") in ("True", "true", "1") and r.get("rtt_estimate_s")}
    )
    figure, axis = plt.subplots()
    if points:
        configured, smoothed, minimum = zip(*points)
        axis.plot(configured, smoothed, "o-", label="RUDP smoothed RTT (includes queueing)")
        axis.plot(configured, minimum, "s--", label="RUDP minimum RTT sample")
        axis.plot(configured, configured, "k:", label="configured RTT (y = x)")
        axis.legend()
    axis.set(title="RTT estimate versus configured RTT",
             xlabel="Configured RTT (ms)", ylabel="RTT (ms)")
    axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(output / "rtt_estimate_vs_configured.png", dpi=160)
    plt.close(figure)


def plot_fairness(rows: list[dict[str, str]], output: Path) -> None:
    import matplotlib.pyplot as plt

    runs: dict[str, dict[str, float]] = {}
    for row in rows:
        if row["experiment"] != "Exp4" or row.get("ok") not in ("True", "true", "1"):
            continue
        value = row.get("overlap_mbps") or row["goodput_mbps"]  # overlap window when measured
        runs.setdefault(row["run"], {})[row["protocol"]] = float(value)
    figure, axis = plt.subplots()
    labels = [f"run {run}" for run in sorted(runs)]
    rudp = [runs[run].get("RUDP", 0) for run in sorted(runs)]
    tcp = [runs[run].get("TCP-RENO", 0) for run in sorted(runs)]
    positions = range(len(labels))
    axis.bar([p - 0.2 for p in positions], rudp, 0.4, label="RUDP")
    axis.bar([p + 0.2 for p in positions], tcp, 0.4, label="TCP Reno")
    axis.set_xticks(list(positions), labels)
    values = [float(row["fairness_index"]) for row in rows
              if row["experiment"] == "Exp4" and row.get("fairness_index") not in (None, "")
              and row["protocol"] == "RUDP"]
    axis.set(title=f"Concurrent flows on a shared bottleneck (Jain index {mean(values):.2f})"
             if values else "Concurrent flows on a shared bottleneck",
             ylabel="Throughput while both flows run (Mbit/s)")
    axis.legend()
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
