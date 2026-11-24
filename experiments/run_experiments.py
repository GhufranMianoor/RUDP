#!/usr/bin/env python3
"""Run the RUDP/TCP comparison matrix and write reproducible CSV results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results"
PAYLOAD_SIZE = 10 * 1024 * 1024


@dataclass(frozen=True)
class Condition:
    experiment: str
    name: str
    delay_ms: float
    loss_pct: float
    rate_mbps: float


def conditions() -> list[Condition]:
    return [
        *(Condition("Exp1", f"loss_{loss:g}", 50, loss, 10)
          for loss in (0, 2, 5)),
        *(Condition("Exp2", f"rtt_{rtt:g}ms", rtt / 2, 0, 10)
          for rtt in (20, 100, 200)),
        *(Condition("Exp3", f"rate_{rate:g}mbps", 50, 0, rate)
          for rate in (5, 10, 20)),
        Condition("Exp4", "fairness", 50, 0, 10),
    ]


def ensure_payload(path: Path) -> str:
    if not path.exists() or path.stat().st_size != PAYLOAD_SIZE:
        pattern = bytes(range(256))
        repetitions, remainder = divmod(PAYLOAD_SIZE, len(pattern))
        path.write_bytes(pattern * repetitions + pattern[:remainder])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_event_log(path: Path) -> tuple[int, float | None]:
    retransmissions = 0
    rtts: list[float] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "retx":
                retransmissions += 1
            if isinstance(event.get("rtt"), (int, float)):
                rtts.append(float(event["rtt"]))
    return retransmissions, statistics.mean(rtts) if rtts else None


def run_rudp(condition: Condition, payload: Path, output: Path, host: str, port: int,
             dry_run: bool) -> dict[str, object]:
    log = output / "logs" / f"{condition.experiment}_{condition.name}_rudp.jsonl"
    received = output / f"{condition.experiment}_{condition.name}_received.bin"
    pcap = output / "pcaps" / f"{condition.experiment}_{condition.name}_rudp.pcapng"
    receiver = [sys.executable, str(ROOT / "apps/recv_file.py"), str(port), str(received)]
    sender = [
        sys.executable, str(ROOT / "apps/send_file.py"), host, str(port), str(payload),
        "--cc", "reno", "--log", str(log),
    ]
    started = time.monotonic()
    capture = None
    if not dry_run:
        interface = "any"
        dumpcap = shutil.which("dumpcap")
        if dumpcap:
            capture = subprocess.Popen(
                [dumpcap, "-q", "-i", interface, "-f", f"udp port {port}",
                 "-w", str(pcap)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        with subprocess.Popen(receiver, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
            subprocess.run(sender, cwd=ROOT, check=True, timeout=300)
        if capture is not None:
            capture.terminate()
            capture.wait(timeout=10)
    elapsed = time.monotonic() - started
    if dry_run:
        elapsed = 0.1
    retransmissions, rtt = parse_event_log(log)
    goodput = PAYLOAD_SIZE * 8 / elapsed / 1_000_000 if elapsed else 0.0
    return {
        "protocol": "RUDP",
        "pcap": str(pcap),
        "completion_time_s": elapsed,
        "goodput_mbps": goodput,
        "retransmissions": retransmissions,
        "rtt_estimate_s": rtt,
        "command": shlex.join(sender),
    }


def run_tcp(condition: Condition, payload: Path, output: Path, host: str, port: int,
            algorithm: str, dry_run: bool) -> dict[str, object]:
    if shutil.which("iperf3") is None and not dry_run:
        raise RuntimeError("iperf3 is required for TCP experiments")
    server = ["iperf3", "-s", "-1", "-p", str(port), "-J"]
    client = [
        "iperf3", "-c", host, "-p", str(port), "-C", algorithm,
        "-n", str(PAYLOAD_SIZE), "-J",
    ]
    started = time.monotonic()
    output_json = ""
    if not dry_run:
        with subprocess.Popen(server, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
            result = subprocess.run(client, cwd=ROOT, check=True, capture_output=True, text=True, timeout=300)
            output_json = result.stdout
    elapsed = time.monotonic() - started
    if dry_run:
        elapsed = 0.1
    goodput = PAYLOAD_SIZE * 8 / elapsed / 1_000_000 if elapsed else 0.0
    if output_json:
        try:
            end = json.loads(output_json)["end"]["sum_sent"]
            goodput = float(end.get("bits_per_second", goodput * 1_000_000)) / 1_000_000
            elapsed = float(end.get("seconds", elapsed))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return {
        "protocol": f"TCP-{algorithm.upper()}",
        "completion_time_s": elapsed,
        "goodput_mbps": goodput,
        "retransmissions": "",
        "rtt_estimate_s": "",
        "command": shlex.join(client),
    }


def jains_fairness(values: list[float]) -> float:
    if not values or not any(values):
        return 0.0
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--base-port", type=int, default=19000)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")

    output = args.results.resolve()
    (output / "logs").mkdir(parents=True, exist_ok=True)
    (output / "pcaps").mkdir(parents=True, exist_ok=True)
    payload = output / "payload-10MiB.bin"
    payload_hash = ensure_payload(payload)
    raw: list[dict[str, object]] = []
    for condition in conditions():
        for run in range(1, args.runs + 1):
            port = args.base_port + len(raw)
            base = {
                "experiment": condition.experiment,
                "condition": condition.name,
                "run": run,
                "delay_ms": condition.delay_ms,
                "loss_pct": condition.loss_pct,
                "rate_mbps": condition.rate_mbps,
                "payload_sha256": payload_hash,
                "pcap": str(output / "pcaps" / f"{condition.experiment}_{condition.name}_{run}.pcapng"),
            }
            for result in (
                run_rudp(condition, payload, output, args.host, port, args.dry_run),
                run_tcp(condition, payload, output, args.host, port + 1, "reno", args.dry_run),
                run_tcp(condition, payload, output, args.host, port + 2, "cubic", args.dry_run),
            ):
                raw.append({**base, **result})
            if condition.experiment == "Exp4":
                fairness = jains_fairness([
                    float(raw[-3]["goodput_mbps"]),
                    float(raw[-2]["goodput_mbps"]),
                ])
                raw[-3]["fairness_index"] = fairness

    raw_path = output / "raw.csv"
    fields = sorted({key for row in raw for key in row})
    with raw_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(raw)

    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in raw:
        key = (str(row["experiment"]), str(row["condition"]), str(row["protocol"]))
        groups.setdefault(key, []).append(row)
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "experiment", "condition", "protocol", "runs",
            "mean_goodput_mbps", "stddev_goodput_mbps",
            "range_goodput_mbps", "mean_completion_time_s", "stddev_completion_time_s",
            "mean_retransmissions", "stddev_retransmissions",
            "mean_rtt_estimate_s", "stddev_rtt_estimate_s",
        ])
        for (experiment, condition, protocol), rows in sorted(groups.items()):
            def values(field: str) -> list[float]:
                return [
                    float(row[field]) for row in rows
                    if row.get(field) not in (None, "")
                    and str(row.get(field)).strip() != ""
                ]

            goodput = values("goodput_mbps")
            completion = values("completion_time_s")
            retransmissions = values("retransmissions")
            rtt = values("rtt_estimate_s")

            def spread(items: list[float]) -> tuple[float, float]:
                if not items:
                    return 0.0, 0.0
                mean_value = statistics.mean(items)
                stddev = statistics.stdev(items) if len(items) > 1 else 0.0
                return mean_value, stddev

            mean_goodput, stddev_goodput = spread(goodput)
            mean_completion, stddev_completion = spread(completion)
            mean_retx, stddev_retx = spread(retransmissions)
            mean_rtt, stddev_rtt = spread(rtt)
            writer.writerow([
                experiment, condition, protocol, len(rows),
                mean_goodput, stddev_goodput,
                f"{max(goodput) - min(goodput):.6g}" if goodput else "0",
                mean_completion, stddev_completion,
                mean_retx, stddev_retx, mean_rtt, stddev_rtt,
            ])


if __name__ == "__main__":
    main()
