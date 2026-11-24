#!/usr/bin/env python3
"""Capture the five pcapng traces the project needs, inside the Mininet topology.

    sudo .venv/bin/python tools/capture_evidence.py [--results results]

Scenario            Network (RTT / loss per direction / rate)   Transfer
handshake           50 ms / 0% / 10 Mbit                        50 KB, SYN, SYN+ACK, ACK visible
loss-recovery       100 ms / 2% / 10 Mbit                       3 MiB, retransmissions + SACK blocks
window-growth       100 ms / 0% / 10 Mbit                       4 MiB, slow start then congestion avoidance
teardown            50 ms / 0% / 10 Mbit                        50 KB, FIN and FIN+ACK visible
flow-control        100 ms / 1% / 10 Mbit                       1 MiB, receiver window 16 packets:
                                                                at most 16 DATA in flight; the ACK
                                                                'win' field drops while holes are open

Each trace is recorded inside R1 on its LAN-side interface (the sender's side of
the bottleneck), so originals, retransmissions and returning ACKs are all
captured. The sender's JSON-lines event log for the same run is saved next to
the pcapng. Text excerpts decoded with tools/rudp_dissector.lua are written to
results/evidence/ when tshark can run Lua (Wireshark disables Lua for root, so
the script runs it as $SUDO_USER).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "topology"))

SCENARIOS = [
    # name, one-way delay, loss, rate, payload bytes, receive window (None = default)
    ("handshake", "25ms", "0%", "10mbit", 50_000, None),
    ("loss-recovery", "50ms", "2%", "10mbit", 3 * 1024 * 1024, None),
    ("window-growth", "50ms", "0%", "10mbit", 4 * 1024 * 1024, None),
    ("teardown", "25ms", "0%", "10mbit", 50_000, None),
    ("flow-control", "50ms", "1%", "10mbit", 1024 * 1024, 16),
]
DISSECTOR_FILTERS = {
    "handshake": "rudpcn.flags.syn == 1 || frame.number <= 12",
    "loss-recovery": "rudpcn.sack_count > 0",
    "window-growth": "rudpcn",
    "teardown": "rudpcn.flags.fin == 1",
    "flow-control": "rudpcn",
}


def capture_command(interface: str, pcap: Path) -> list[str]:
    if shutil.which("dumpcap"):
        return ["dumpcap", "-q", "-i", interface, "-f", "udp port 9000", "-s", "512", "-w", str(pcap)]
    raise SystemExit("dumpcap is required for pcapng output (sudo apt install wireshark-common tshark)")


def tshark_as_user(args: list[str]) -> str | None:
    """Run tshark as the invoking user: Wireshark refuses Lua as root."""

    user = os.environ.get("SUDO_USER")
    if os.geteuid() == 0 and not user:
        return None
    command = ["tshark", *args]
    if os.geteuid() == 0:
        command = ["runuser", "-u", user, "--", *command]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument("--only", nargs="+", choices=[s[0] for s in SCENARIOS])
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run as root: sudo .venv/bin/python tools/capture_evidence.py")
    import topo

    pcaps = args.results / "pcaps"
    evidence = args.results / "evidence"
    logs = args.results / "logs"
    for directory in (pcaps, evidence, logs):
        directory.mkdir(parents=True, exist_ok=True)
    payload_dir = args.results / "work"
    payload_dir.mkdir(exist_ok=True)

    failures = 0
    for name, delay, loss, rate, size, window in SCENARIOS:
        window_args = ["--window", str(window)] if window else []
        if args.only and name not in args.only:
            continue
        print(f"\n=== {name}: one-way delay {delay}, loss {loss}, rate {rate}, {size} bytes ===", flush=True)
        payload = payload_dir / f"evidence-{size}.bin"
        if not payload.exists() or payload.stat().st_size != size:
            payload.write_bytes(os.urandom(size))
        received = payload_dir / f"evidence-{name}.received"
        pcap = pcaps / f"{name}.pcapng"
        log = logs / f"evidence_{name}_rudp.jsonl"
        log.unlink(missing_ok=True)
        net = topo.build_network(delay, loss, rate)
        capture = None
        try:
            capture = net["R1"].popen(
                capture_command(net.interface_names["R1_lan"], pcap),
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            time.sleep(1.0)
            receiver = net["h2"].popen(
                [sys.executable, str(ROOT / "apps/recv_file.py"), "9000", str(received),
                 "--idle-timeout", "60", *window_args],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            time.sleep(0.5)
            sender = net["h1"].popen(
                [sys.executable, str(ROOT / "apps/send_file.py"), "10.0.3.10", "9000", str(payload),
                 "--log", str(log), *window_args], stdout=subprocess.PIPE, text=True,
            )
            out, _ = sender.communicate(timeout=600)
            receiver.wait(timeout=60)
            ok = received.exists() and received.read_bytes() == payload.read_bytes()
            print(f"  transfer {'OK (SHA-256 identical)' if ok else 'FAILED'}: {out.strip()}")
            failures += 0 if ok else 1
        finally:
            if capture is not None:
                time.sleep(0.5)
                capture.terminate()
                capture.wait(timeout=10)
            net.stop()
            received.unlink(missing_ok=True)
        pcap.chmod(0o644)
        size_kb = pcap.stat().st_size / 1024
        print(f"  wrote {pcap} ({size_kb:.0f} KiB)")

        text = tshark_as_user([
            "-X", f"lua_script:{ROOT / 'tools/rudp_dissector.lua'}", "-r", str(pcap),
            "-Y", DISSECTOR_FILTERS[name],
        ])
        if text:
            excerpt = evidence / f"{name}_decoded.txt"
            excerpt.write_text("\n".join(text.splitlines()[:400]) + "\n")
            print(f"  decoded excerpt -> {excerpt}")
        else:
            print("  (tshark could not decode here; open the pcapng in Wireshark with the Lua dissector)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
