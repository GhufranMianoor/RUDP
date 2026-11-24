#!/usr/bin/env python3
"""Run the RUDP vs TCP experiment matrix inside the Mininet topology.

Must run as root on the Ubuntu lab machine (``sudo .venv/bin/python
experiments/run_experiments.py``). For every condition it builds a fresh
network with ``topology/topo.py`` (delay / loss / rate on the R1-R2 link),
then for each run:

* ping     h1 -> h2, 20 probes on the idle path: the independently measured RTT
* RUDP     h1 -> h2, UDP 9000, verified by SHA-256 (Reno controller; add
  ``--rudp-cc reno cubic`` to also run the CUBIC controller)
* TCP Reno h3 -> h4, iperf3 on TCP 5201 (``-C reno``)
* TCP CUBIC h3 -> h4, iperf3 on TCP 5201 (``-C cubic``)

The fairness experiment (Exp4) instead runs RUDP (h1 -> h2) and iperf3
(h3 -> h4) *at the same time* over the shared bottleneck, at RTT 20, 100 and
200 ms, each against TCP Reno (``fairness_reno_rtt*``) and against TCP CUBIC
(``fairness_cubic_rtt*``).

Goodput is application bytes divided by the transfer time: for RUDP the
sender-measured time from the end of the handshake to the FIN being
acknowledged; for TCP, iperf3's ``sum_received`` (bytes and seconds seen by
the receiver). Packet captures are taken inside R1 on its sender-side (LAN)
interface for the first run of each condition; RUDP event logs for every run.
Exp4 fairness uses both flows' throughput over the window in which both are
running (``experiments/fairness.py``), not each flow over its own duration.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "topology"))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT))

from fairness import (  # noqa: E402  (experiments/fairness.py)
    iperf_delivery_curve,
    jains_fairness,
    overlap_throughputs,
    rudp_delivery_curve,
)

RUDP_PORT = 9000
IPERF_PORT = 5201
H1, H2, H3, H4 = "h1", "h2", "h3", "h4"
DST_RUDP, DST_TCP = "10.0.3.10", "10.0.3.11"
MIB = 1024 * 1024


@dataclass(frozen=True)
class Condition:
    experiment: str
    name: str
    delay_ms: float  # one-way, on each bottleneck egress: RTT = 2 x delay
    loss_pct: float  # per direction
    rate_mbps: float
    concurrent: bool = False
    tcp_cc: str = "reno"  # Exp4: the TCP algorithm competing with RUDP

    @property
    def rtt_ms(self) -> float:
        return 2 * self.delay_ms


def conditions() -> list[Condition]:
    return [
        *(Condition("Exp1", f"loss_{loss:g}", 50, loss, 10) for loss in (0, 2, 5)),
        *(Condition("Exp2", f"rtt_{rtt:g}ms", rtt / 2, 0, 10) for rtt in (20, 100, 200)),
        *(Condition("Exp3", f"rate_{rate:g}mbps", 50, 0, rate) for rate in (5, 10, 20)),
        # Fairness under 3 network conditions (RTT 20 / 100 / 200 ms), each
        # against TCP Reno and against TCP CUBIC.
        *(Condition("Exp4", f"fairness_{cc}_rtt{rtt:g}ms", rtt / 2, 0, 10, concurrent=True, tcp_cc=cc)
          for cc in ("reno", "cubic") for rtt in (20, 100, 200)),
    ]


def ensure_payload(path: Path, size: int) -> str:
    """Deterministic pseudo-random payload (reordering/corruption cannot hide in a repeating pattern)."""

    if not path.exists() or path.stat().st_size != size:
        path.write_bytes(random.Random(20260101).randbytes(size))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------- helpers

def start_capture(net, pcap: Path):
    """Capture UDP 9000 on R1's LAN-side interface, inside R1's namespace.

    That is the sender's side of the bottleneck: every transmission (including
    the originals of packets the bottleneck later drops, and every
    retransmission) is visible, plus all returning ACKs and SACK blocks.
    """

    interface = net.interface_names["R1_lan"]
    pcap.parent.mkdir(parents=True, exist_ok=True)
    dumpcap = shutil.which("dumpcap")
    if dumpcap:
        command = [dumpcap, "-q", "-i", interface, "-f", f"udp port {RUDP_PORT}",
                   "-s", "256", "-w", str(pcap)]
    elif shutil.which("tcpdump"):
        pcap = pcap.with_suffix(".pcap")
        command = ["tcpdump", "-i", interface, "-s", "256", "-U", "-w", str(pcap),
                   f"udp port {RUDP_PORT}"]
    else:
        return None
    process = net["R1"].popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(1.0)  # let the capture attach before traffic starts
    return process


def stop_capture(process) -> None:
    if process is None:
        return
    time.sleep(0.5)  # let the last packets reach the capture
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def start_rudp(net, payload: Path, work: Path, tag: str, window: int, log: Path | None,
               cc: str = "reno"):
    """Start receiver on h2 and sender on h1. Returns handles; sender not yet waited on."""

    received = work / f"{tag}_received.bin"
    summary = work / f"{tag}_summary.json"
    received.unlink(missing_ok=True)
    summary.unlink(missing_ok=True)
    receiver = net[H2].popen(
        [sys.executable, str(ROOT / "apps/recv_file.py"), str(RUDP_PORT), str(received),
         "--idle-timeout", "60"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(0.5)
    command = [sys.executable, str(ROOT / "apps/send_file.py"), DST_RUDP, str(RUDP_PORT),
               str(payload), "--cc", cc, "--summary", str(summary), "--window", str(window)]
    if log is not None:
        command += ["--log", str(log)]
    sender = net[H1].popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return {"receiver": receiver, "sender": sender, "received": received, "summary": summary,
            "command": " ".join(command), "started": time.monotonic(), "cc": cc}


def finish_rudp(handle: dict, payload_sha: str, timeout: float) -> dict:
    sender, receiver = handle["sender"], handle["receiver"]
    # "RUDP" is the Reno controller (the default); other controllers are labelled.
    label = "RUDP" if handle.get("cc", "reno") == "reno" else f"RUDP-{handle['cc'].upper()}"
    row: dict[str, object] = {"protocol": label, "command": handle["command"], "ok": False}
    try:
        sender.wait(timeout=timeout)
        receiver.wait(timeout=60)
    except subprocess.TimeoutExpired:
        row["error"] = "timeout"
        return _kill(row, sender, receiver)
    if handle["summary"].exists():
        summary = json.loads(handle["summary"].read_text())
        sha_ok = handle["received"].exists() and sha256_file(handle["received"]) == payload_sha
        ok = bool(summary.get("ok")) and sha_ok and receiver.returncode == 0
        row.update(
            ok=ok,
            sha256_ok=sha_ok,
            error=summary.get("error", "") or ("" if ok else "sha256 mismatch or receiver error"),
            completion_time_s=summary["seconds"],
            goodput_mbps=summary["goodput_mbps"] if ok else 0.0,
            retransmissions=summary.get("retransmissions", ""),
            fast_retransmits=summary.get("fast_retransmits", ""),
            timeouts=summary.get("timeouts", ""),
            rtt_estimate_s=summary.get("srtt") or "",
            min_rtt_s=summary.get("min_rtt") or "",
            rtt_samples=summary.get("samples", ""),
            # Smoothed RTT above the path minimum: mostly bottleneck queueing delay.
            queue_delay_ms=((summary["srtt"] - summary["min_rtt"]) * 1000
                            if summary.get("srtt") and summary.get("min_rtt") else ""),
        )
    else:
        row["error"] = "sender produced no summary"
    handle["received"].unlink(missing_ok=True)  # 10 MiB each; the SHA is what we keep
    return row


def _kill(row: dict, *processes) -> dict:
    for process in processes:
        if process.poll() is None:
            process.kill()
    return row


def start_iperf_server(net, keep_output: bool = False):
    """iperf3 server on h4; returns once it is actually listening.

    With ``keep_output`` the server's JSON (receiver-side 0.5 s intervals) is
    kept, which Exp4 uses to measure TCP over the overlap window.
    """

    server = net[H4].popen(
        ["iperf3", "-s", "-1", "-p", str(IPERF_PORT), "-J", "-i", "0.5"],
        stdout=subprocess.PIPE if keep_output else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True,
    )
    for _ in range(50):
        if "LISTEN" in net[H4].cmd(f"ss -ltn 'sport = :{IPERF_PORT}'"):
            return server
        time.sleep(0.1)
    server.kill()
    raise RuntimeError("iperf3 server did not start listening on h4")


def start_iperf_client(net, server, algorithm: str, nbytes: int) -> dict:
    command = ["iperf3", "-c", DST_TCP, "-p", str(IPERF_PORT), "-C", algorithm,
               "-n", str(nbytes), "-J"]
    client = net[H3].popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return {"server": server, "client": client, "command": " ".join(command),
            "algorithm": algorithm, "nbytes": nbytes, "started": time.monotonic()}


def finish_iperf(handle: dict, timeout: float) -> dict:
    client, server = handle["client"], handle["server"]
    row: dict[str, object] = {
        "protocol": f"TCP-{handle['algorithm'].upper()}",
        "command": handle["command"], "ok": False,
    }
    try:
        out, err = client.communicate(timeout=timeout)
        if server.stdout is not None:
            handle["server_output"], _ = server.communicate(timeout=30)
        else:
            server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        row["error"] = "timeout"
        return _kill(row, client, server)
    try:
        end = json.loads(out)["end"]
        received, sent = end["sum_received"], end["sum_sent"]
        # iperf3 -n ends the test when the sender has written all bytes; data
        # still queued at the bottleneck is not yet in sum_received (about
        # queue + BDP, ~0.3 MB), so a 95% threshold is used. With >= 10 MiB
        # payloads that under-counts TCP goodput by <= ~3%.
        row.update(
            ok=sent["bytes"] >= handle["nbytes"] and received["bytes"] >= handle["nbytes"] * 0.95,
            goodput_mbps=received["bits_per_second"] / 1e6,   # receiver side, not sum_sent
            completion_time_s=received["seconds"],
            retransmissions=sent.get("retransmits", ""),
            bytes_received=received["bytes"],
            bytes_sent=sent["bytes"],
            rtt_estimate_s="",
        )
        if not row["ok"]:
            row["error"] = f"only {received['bytes']} of {handle['nbytes']} bytes received"
    except (KeyError, ValueError, json.JSONDecodeError):
        row["error"] = (err or out or "no iperf3 output").strip()[:200]
    return row


def parse_ping(output: str) -> dict:
    """Extract loss and min/avg/max/mdev RTT (ms) from Linux ``ping`` output."""

    import re
    result: dict[str, float | str] = {"ping_loss_pct": "", "ping_min_rtt_ms": "",
                                      "ping_avg_rtt_ms": "", "ping_max_rtt_ms": "",
                                      "ping_mdev_ms": ""}
    loss = re.search(r"([\d.]+)% packet loss", output)
    if loss:
        result["ping_loss_pct"] = float(loss.group(1))
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", output)
    if rtt:
        result.update(ping_min_rtt_ms=float(rtt.group(1)), ping_avg_rtt_ms=float(rtt.group(2)),
                      ping_max_rtt_ms=float(rtt.group(3)), ping_mdev_ms=float(rtt.group(4)))
    return result


def measure_ping(net, count: int) -> dict:
    """RTT h1 -> h2 measured by ICMP on the idle path, independent of RUDP's estimator."""

    if count <= 0:
        return {}
    return parse_ping(net[H1].cmd(f"ping -n -q -c {count} -i 0.2 -W 2 {DST_RUDP}"))


# ------------------------------------------------------------------------ main

def run_condition(cond: Condition, args, payload: Path, payload_sha: str, work: Path) -> list[dict]:
    import topo  # topology/topo.py (needs Mininet; imported lazily so --list works anywhere)

    print(f"\n=== {cond.experiment} {cond.name}: RTT {cond.rtt_ms:g} ms, "
          f"loss {cond.loss_pct:g}%/direction, rate {cond.rate_mbps:g} Mbit/s ===", flush=True)
    net = topo.build_network(f"{cond.delay_ms:g}ms", f"{cond.loss_pct:g}%",
                             f"{cond.rate_mbps:g}mbit", args.queue_packets)
    rows: list[dict] = []
    try:
        for run in range(1, args.runs + 1):
            first = run <= args.capture_runs
            stem = f"{cond.experiment}_{cond.name}_run{run}"
            base = {
                "experiment": cond.experiment, "condition": cond.name, "run": run,
                "delay_ms": cond.delay_ms, "rtt_ms": cond.rtt_ms, "loss_pct": cond.loss_pct,
                "rate_mbps": cond.rate_mbps, "payload_bytes": payload.stat().st_size,
                "payload_sha256": payload_sha, "bottleneck_queue_packets": args.queue_packets,
                "concurrent": cond.concurrent,
            }
            # Independent RTT reference on the idle path, before any transfer.
            base.update(measure_ping(net, args.ping_count))
            # Every run is logged (logging is buffered and does not slow the
            # sender), so logged and unlogged runs are measured alike.
            log = args.results / "logs" / f"{stem}_rudp.jsonl"
            log.unlink(missing_ok=True)
            pcap = args.results / "pcaps" / f"{stem}.pcapng"
            results: list[dict] = []
            if cond.concurrent:
                capture = start_capture(net, pcap) if first else None
                nbytes = payload.stat().st_size
                server = start_iperf_server(net, keep_output=True)  # ready before either client starts
                t0 = time.time()
                rudp = start_rudp(net, payload, work, stem, args.window, log)
                tcp = start_iperf_client(net, server, cond.tcp_cc, nbytes)  # starts with RUDP's sender
                tcp_row = finish_iperf(tcp, args.run_timeout)
                rudp_row = finish_rudp(rudp, payload_sha, args.run_timeout)
                stop_capture(capture)
                for row in (rudp_row, tcp_row):
                    row["flow"] = "concurrent"
                    row["wall_start"] = t0
                fairness = own_duration = ""
                if rudp_row.get("ok") and tcp_row.get("ok"):
                    # Reference only: each flow over its own duration (optimistic).
                    own_duration = jains_fairness([float(rudp_row["goodput_mbps"]),
                                                   float(tcp_row["goodput_mbps"])])
                    overlap = None
                    try:
                        overlap = overlap_throughputs(
                            rudp_delivery_curve(log),
                            iperf_delivery_curve(tcp.get("server_output", ""), tcp["started"]),
                        )
                    except (OSError, ValueError, KeyError) as exc:
                        rudp_row["error"] = tcp_row["error"] = f"overlap fairness failed: {exc}"
                    if overlap is not None:
                        rudp_mbps, tcp_mbps, window = overlap
                        fairness = jains_fairness([rudp_mbps, tcp_mbps])
                        total = rudp_mbps + tcp_mbps
                        rudp_row.update(overlap_mbps=rudp_mbps, overlap_window_s=window,
                                        share_of_total=rudp_mbps / total if total else "")
                        tcp_row.update(overlap_mbps=tcp_mbps, overlap_window_s=window,
                                       share_of_total=tcp_mbps / total if total else "")
                for row in (rudp_row, tcp_row):
                    row["fairness_index"] = fairness
                    row["fairness_index_own_duration"] = own_duration
                results = [rudp_row, tcp_row]
            else:
                for cc in args.rudp_cc:
                    # The capture and the default log name belong to the Reno run.
                    tag = stem if cc == "reno" else f"{stem}_{cc}"
                    cc_log = log if cc == "reno" else args.results / "logs" / f"{tag}_rudp.jsonl"
                    cc_log.unlink(missing_ok=True)
                    capture = start_capture(net, pcap) if first and cc == "reno" else None
                    results.append(finish_rudp(
                        start_rudp(net, payload, work, tag, args.window, cc_log, cc),
                        payload_sha, args.run_timeout))
                    stop_capture(capture)
                for algorithm in ("reno", "cubic"):
                    server = start_iperf_server(net)
                    results.append(finish_iperf(
                        start_iperf_client(net, server, algorithm, payload.stat().st_size),
                        args.run_timeout))
                for row in results:
                    row["flow"] = "alone"
            for row in results:
                rows.append({**base, **row})
                status = "ok" if row.get("ok") else f"FAILED ({row.get('error', '?')})"
                print(f"  run {run} {row['protocol']:<10} "
                      f"{float(row.get('goodput_mbps') or 0):7.2f} Mbit/s  {status}", flush=True)
    finally:
        net.stop()
    return rows


def write_outputs(rows: list[dict], output: Path) -> None:
    fields = sorted({key for row in rows for key in row})
    with (output / "raw.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    def spread(values: list[float]) -> tuple[float, float]:
        if not values:
            return "", ""
        return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["experiment"], row["condition"], row["protocol"]), []).append(row)
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "experiment", "condition", "protocol", "runs", "runs_ok",
            "mean_goodput_mbps", "stddev_goodput_mbps",
            "mean_completion_time_s", "stddev_completion_time_s",
            "mean_retransmissions", "stddev_retransmissions",
            "mean_rtt_estimate_s", "stddev_rtt_estimate_s",
            "mean_queue_delay_ms", "stddev_queue_delay_ms",
            "mean_fairness_index", "stddev_fairness_index",
            "min_fairness_index", "max_fairness_index",
            "mean_share_of_total", "stddev_share_of_total",
            "mean_min_rtt_ms", "mean_ping_avg_rtt_ms", "stddev_ping_avg_rtt_ms",
            "mean_ping_min_rtt_ms",
        ])
        for (experiment, condition, protocol), group in sorted(groups.items()):
            good = [row for row in group if row.get("ok")]

            def column(name: str) -> list[float]:
                return [float(row[name]) for row in good if row.get(name) not in (None, "")]

            goodput = spread(column("goodput_mbps"))
            completion = spread(column("completion_time_s"))
            retx = spread(column("retransmissions"))
            rtt = spread(column("rtt_estimate_s"))
            queue = spread(column("queue_delay_ms"))
            fairness = column("fairness_index")
            share = column("share_of_total")
            min_rtt = column("min_rtt_s")
            ping_avg = spread(column("ping_avg_rtt_ms"))
            ping_min = column("ping_min_rtt_ms")
            writer.writerow([
                experiment, condition, protocol, len(group), len(good),
                *goodput, *completion, *retx, *rtt, *queue,
                *spread(fairness),
                min(fairness) if fairness else "",
                max(fairness) if fairness else "",
                *spread(share),
                statistics.mean(min_rtt) * 1000 if min_rtt else "",
                *ping_avg,
                statistics.mean(ping_min) if ping_min else "",
            ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per condition (default 3)")
    parser.add_argument("--payload-mib", type=float, default=10)
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--only", nargs="+", metavar="EXP",
                        help="run only these experiments, e.g. Exp1 Exp4")
    parser.add_argument("--capture-runs", type=int, default=1,
                        help="write pcapng captures for the first N runs of each condition "
                             "(RUDP event logs are written for every run)")
    parser.add_argument("--queue-packets", type=int, default=100)
    parser.add_argument("--window", type=int, default=1024)
    parser.add_argument("--rudp-cc", nargs="+", choices=["reno", "cubic"], default=["reno"],
                        help="RUDP congestion controllers to run in Exp1-3 (default: reno). "
                             "Exp4 always uses Reno RUDP.")
    parser.add_argument("--ping-count", type=int, default=20,
                        help="ICMP probes h1 -> h2 before each run for the measured RTT (0 = off)")
    parser.add_argument("--run-timeout", type=float, default=900,
                        help="seconds before one transfer is declared failed")
    parser.add_argument("--list", action="store_true",
                        help="print the planned matrix and exit (no network, no results)")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    selected = [c for c in conditions() if not args.only or c.experiment in args.only]
    if not selected:
        parser.error(f"no experiments match {args.only}")

    if args.list:
        for cond in selected:
            print(f"{cond.experiment:5} {cond.name:24} RTT {cond.rtt_ms:6g} ms  "
                  f"loss {cond.loss_pct:g}%  rate {cond.rate_mbps:g} Mbit/s  x{args.runs} runs"
                  + (f"  (RUDP and TCP {cond.tcp_cc.upper()} concurrently)" if cond.concurrent
                     else f"  (RUDP {'+'.join(args.rudp_cc)}, TCP Reno, TCP CUBIC)"))
        return 0

    import os
    if os.geteuid() != 0:
        raise SystemExit("run as root: sudo .venv/bin/python experiments/run_experiments.py")
    args.results = args.results.resolve()
    for sub in ("logs", "pcaps"):
        (args.results / sub).mkdir(parents=True, exist_ok=True)
    work = args.results / "work"
    work.mkdir(exist_ok=True)
    payload = work / f"payload-{args.payload_mib:g}MiB.bin"
    payload_sha = ensure_payload(payload, int(args.payload_mib * MIB))

    rows: list[dict] = []
    try:
        for cond in selected:
            rows.extend(run_condition(cond, args, payload, payload_sha, work))
            write_outputs(rows, args.results)  # keep partial results if a later condition fails
    finally:
        if rows:
            write_outputs(rows, args.results)
    failed = [row for row in rows if not row.get("ok")]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} transfers succeeded; "
          f"results in {args.results}/raw.csv and summary.csv")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
