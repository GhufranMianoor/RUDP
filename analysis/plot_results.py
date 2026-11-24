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
    """Exp2: RUDP's RTT estimate against the RTT measured by ping and the configured RTT."""

    import matplotlib.pyplot as plt

    def ok(row: dict[str, str]) -> bool:
        return row.get("ok") in ("True", "true", "1")

    series: dict[str, dict[float, list[float]]] = {
        "RUDP smoothed RTT (includes queueing)": {},
        "RUDP minimum RTT sample": {},
        "ping average RTT (idle path)": {},
    }
    for row in rows:
        if row["experiment"] != "Exp2" or row["protocol"] != "RUDP" or not ok(row):
            continue
        configured = float(row["rtt_ms"])
        for label, field, scale in (
            ("RUDP smoothed RTT (includes queueing)", "rtt_estimate_s", 1000.0),
            ("RUDP minimum RTT sample", "min_rtt_s", 1000.0),
            ("ping average RTT (idle path)", "ping_avg_rtt_ms", 1.0),
        ):
            if row.get(field) not in (None, ""):
                series[label].setdefault(configured, []).append(float(row[field]) * scale)

    figure, axis = plt.subplots()
    markers = iter(("o-", "s--", "^-."))
    plotted = False
    for label, values in series.items():
        marker = next(markers)
        if not values:
            continue
        xs = sorted(values)
        axis.errorbar(xs, [mean(values[x]) for x in xs],
                      yerr=[stdev(values[x]) if len(values[x]) > 1 else 0 for x in xs],
                      fmt=marker, capsize=3, label=label)
        plotted = True
    if plotted:
        configured = sorted({x for values in series.values() for x in values})
        axis.plot(configured, configured, "k:", label="configured RTT (y = x)")
        axis.legend()
    axis.set(title="RTT estimate versus measured RTT (Exp2)",
             xlabel="Configured RTT (ms)", ylabel="RTT (ms)")
    axis.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(output / "rtt_estimate_vs_measured.png", dpi=160)
    plt.close(figure)


def plot_fairness(rows: list[dict[str, str]], output: Path) -> None:
    """One bar chart per Exp4 condition (RUDP against TCP Reno, RUDP against TCP CUBIC)."""

    import matplotlib.pyplot as plt

    exp4 = [row for row in rows if row["experiment"] == "Exp4"]
    for condition in sorted({row["condition"] for row in exp4}):
        runs: dict[str, dict[str, float]] = {}
        tcp_name = "TCP"
        for row in exp4:
            if row["condition"] != condition or row.get("ok") not in ("True", "true", "1"):
                continue
            value = row.get("overlap_mbps") or row["goodput_mbps"]  # overlap window when measured
            key = "RUDP" if row["protocol"].startswith("RUDP") else "TCP"
            if key == "TCP":
                tcp_name = row["protocol"].replace("-", " ").replace("RENO", "Reno")
            runs.setdefault(row["run"], {})[key] = float(value)
        figure, axis = plt.subplots()
        ordered = sorted(runs, key=lambda r: int(r) if str(r).isdigit() else r)
        positions = range(len(ordered))
        axis.bar([p - 0.2 for p in positions], [runs[r].get("RUDP", 0) for r in ordered], 0.4,
                 label="RUDP (Reno)")
        axis.bar([p + 0.2 for p in positions], [runs[r].get("TCP", 0) for r in ordered], 0.4,
                 label=tcp_name)
        axis.set_xticks(list(positions), [f"run {r}" for r in ordered])
        values = [float(row["fairness_index"]) for row in exp4
                  if row["condition"] == condition and row.get("fairness_index") not in (None, "")
                  and row["protocol"].startswith("RUDP")]
        title = f"RUDP vs {tcp_name} on a shared bottleneck"
        axis.set(title=f"{title} (Jain {mean(values):.2f})" if values else title,
                 ylabel="Throughput while both flows run (Mbit/s)")
        axis.legend()
        figure.tight_layout()
        figure.savefig(output / f"{condition}.png", dpi=160)
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
