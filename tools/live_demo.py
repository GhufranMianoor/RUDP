#!/usr/bin/env python3
"""Live POC demo: a RUDP file transfer h1 -> h2 across both routers while the
bottleneck's loss and delay are changed mid-transfer.

    sudo .venv/bin/python tools/live_demo.py                 # default three phases
    sudo .venv/bin/python tools/live_demo.py --pause         # wait for Enter between steps
    sudo .venv/bin/python tools/live_demo.py --cli           # Mininet CLI before teardown
    sudo .venv/bin/python tools/live_demo.py \\
        --phases "0:25ms:0%,10:25ms:2%,20:100ms:0%" --rate 10mbit --size-mib 30

What the audience sees, in order:

1. Interfaces and routing tables of h1, h2, R1 and R2, and the netem/tbf qdiscs
   on the R1-R2 bottleneck.
2. ``traceroute`` h1 -> h2 (10.0.1.1 -> 10.0.2.2 -> 10.0.3.10) and a ping.
3. The transfer, with the sender printing cwnd, ssthresh, state, RTO and
   progress every 0.5 s. At each phase time the script changes netem on both
   bottleneck egress interfaces and announces it, so cwnd reacting to loss
   (fast recovery, halving) and RTT (slower growth) is visible live.
4. SHA-256 of sent and received files, the sender's summary, and a cwnd plot
   with the phase changes marked (``results/demo/cwnd_demo.png``).

``--phases`` is ``seconds:one-way-delay:loss`` per phase, comma separated; the
first phase starts at 0 and sets the initial network. RTT = 2 x delay.
With ``--capture``, a pcapng of the whole demo is written inside R1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "topology"))


def parse_phases(text: str) -> list[tuple[float, str, str]]:
    phases = []
    for part in text.split(","):
        seconds, delay, loss = part.strip().split(":")
        phases.append((float(seconds), delay, loss))
    phases.sort()
    if not phases or phases[0][0] != 0:
        raise ValueError("the first phase must start at 0")
    return phases


def banner(text: str, pause: bool = False) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}", flush=True)
    if pause:
        input("(press Enter to continue) ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def set_netem(net, delay: str, loss: str) -> None:
    """Change delay/loss on both bottleneck egress interfaces; the tbf child stays."""

    for router, key in (("R1", "R1_wan"), ("R2", "R2_wan")):
        interface = net.interface_names[key]
        out = net[router].cmd(
            f"tc qdisc change dev {interface} root handle 1: netem delay {delay} loss {loss} limit 10000"
        )
        if out.strip():
            raise RuntimeError(f"{router}: tc change failed: {out.strip()}")


def plot(log: Path, phases, started_mono: float, output: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping the plot")
        return None
    events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    points = [e for e in events if e.get("event") == "congestion_event"]
    if not points:
        return None
    times = [e["monotonic"] - started_mono for e in points]
    figure, axis = plt.subplots(figsize=(10, 4.5))
    axis.plot(times, [e["cwnd"] for e in points], label="cwnd", linewidth=1)
    axis.plot(times, [min(e["ssthresh"], max(e["cwnd"] for e in points) * 1.2) for e in points],
              label="ssthresh (clipped)", linewidth=1, linestyle="--")
    for seconds, delay, loss in phases[1:]:
        axis.axvline(seconds, color="grey", linestyle=":")
        axis.text(seconds, axis.get_ylim()[1] * 0.95, f" {delay}/{loss}", fontsize=8, va="top")
    axis.set(title=f"RUDP cwnd during the live demo (phases: "
                   f"{', '.join(f'{s:g}s {d} {l}' for s, d, l in phases)})",
             xlabel="seconds since sender start", ylabel="packets")
    axis.grid(alpha=0.3)
    axis.legend()
    figure.tight_layout()
    path = output / "cwnd_demo.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phases", default="0:25ms:0%,10:25ms:2%,20:100ms:0%",
                        help="seconds:one-way-delay:loss, comma separated (default: %(default)s)")
    parser.add_argument("--rate", default="10mbit")
    parser.add_argument("--size-mib", type=float, default=30,
                        help="file size; pick one that outlasts the last phase (default 30)")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--pause", action="store_true", help="wait for Enter between steps")
    parser.add_argument("--capture", action="store_true", help="save a pcapng inside R1")
    parser.add_argument("--cli", action="store_true", help="open the Mininet CLI before teardown")
    args = parser.parse_args()
    phases = parse_phases(args.phases)
    if os.geteuid() != 0:
        raise SystemExit("run as root: sudo .venv/bin/python tools/live_demo.py")
    import topo

    output = args.results / "demo"
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "demo_payload.bin"
    payload.write_bytes(os.urandom(int(args.size_mib * 1024 * 1024)))
    received = output / "demo_received.bin"
    received.unlink(missing_ok=True)
    log = output / "demo_rudp.jsonl"
    log.unlink(missing_ok=True)
    os.chmod(output, 0o777)

    _, delay0, loss0 = phases[0]
    net = topo.build_network(delay0, loss0, args.rate)
    capture = None
    try:
        banner("1. Addressing, routing tables and bottleneck qdiscs", args.pause)
        for name in ("h1", "h2", "R1", "R2"):
            print(f"--- {name}: interfaces")
            print(net[name].cmd("ip -br addr").rstrip())
            print(f"--- {name}: routing table")
            print(net[name].cmd("ip route").rstrip())
        for router, key in (("R1", "R1_wan"), ("R2", "R2_wan")):
            print(f"--- {router} {net.interface_names[key]} (bottleneck egress)")
            print(net[router].cmd(f"tc qdisc show dev {net.interface_names[key]}").rstrip())

        banner("2. Path h1 -> h2 crosses both routers", args.pause)
        print(net["h1"].cmd("traceroute -n -m 5 -w 2 10.0.3.10").rstrip())
        print(net["h1"].cmd("ping -c 3 10.0.3.10").rstrip())

        banner(f"3. RUDP transfer h1 -> h2 ({args.size_mib:g} MiB, rate {args.rate}); "
               f"network changes at {', '.join(f'{s:g}s' for s, _, _ in phases[1:]) or 'none'}",
               args.pause)
        if args.capture and shutil.which("dumpcap"):
            pcap = output / "demo.pcapng"
            capture = net["R1"].popen(
                ["dumpcap", "-q", "-i", net.interface_names["R1_lan"], "-f", "udp port 9000",
                 "-s", "256", "-w", str(pcap)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
        receiver = net["h2"].popen(
            [sys.executable, str(ROOT / "apps/recv_file.py"), "9000", str(received),
             "--idle-timeout", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.5)
        started = time.monotonic()
        sender = net["h1"].popen(
            [sys.executable, str(ROOT / "apps/send_file.py"), "10.0.3.10", "9000", str(payload),
             "--log", str(log), "--progress"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def relay() -> None:
            for line in sender.stderr:
                print("  " + line.rstrip(), flush=True)

        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        for seconds, delay, loss in phases[1:]:
            while sender.poll() is None and time.monotonic() - started < seconds:
                time.sleep(0.05)
            if sender.poll() is not None:
                print(f"  (transfer finished before the {seconds:g}s phase; use a larger --size-mib)")
                break
            set_netem(net, delay, loss)
            print(f"\n  >>> t={seconds:g}s  network now: one-way delay {delay} "
                  f"(RTT {delay} x 2), loss {loss} per direction\n", flush=True)
        summary, _ = sender.communicate(timeout=1800)
        reader.join(timeout=5)
        receiver.wait(timeout=60)
        if capture is not None:
            time.sleep(0.5)
            capture.terminate()
            capture.wait(timeout=10)
            capture = None

        banner("4. Result", args.pause)
        same = received.exists() and sha256(received) == sha256(payload)
        print(f"sent     SHA-256 {sha256(payload)}")
        print(f"received SHA-256 {sha256(received) if received.exists() else '(missing)'}")
        print("MATCH" if same else "MISMATCH")
        print(f"sender summary: {summary.strip()}")
        figure = plot(log, phases, started, output)
        if figure:
            print(f"cwnd plot: {figure}")
        print(f"event log: {log}")
        if args.cli:
            from mininet.cli import CLI
            CLI(net)
        return 0 if same and sender.returncode == 0 else 1
    finally:
        if capture is not None:
            capture.terminate()
        net.stop()
        received.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
