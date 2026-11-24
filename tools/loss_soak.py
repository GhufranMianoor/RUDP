"""Localhost soak test: N transfers at each loss rate through the lossy proxy.

Pass means every SHA-256 matches, nothing hangs, and the receiver exits.

    python tools/loss_soak.py --size-mib 10 --runs 10 --loss 0 2 5
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import socket
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import Connection, ConnectionError
from rudp.controller import RenoController
from tools.lossy_proxy import LossyProxy


def one_run(size: int, loss: float, seed: int) -> dict:
    payload = os.urandom(size)
    outcome: dict = {}

    def receive(sock: socket.socket) -> None:
        try:
            outcome["data"] = Connection(sock, idle_timeout=30.0, linger=1.0).receive_bytes()
        except Exception as exc:
            outcome["rx_error"] = repr(exc)

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    thread = threading.Thread(target=receive, args=(recv_sock,), daemon=True)
    thread.start()
    error = ""
    stats: dict = {}
    seconds = 0.0
    with LossyProxy(recv_sock.getsockname(), drop=loss, seed=seed) as proxy:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            connection = Connection(
                sock, controller=RenoController(initial_ssthresh=1024.0), stall_timeout=30.0
            )
            try:
                connection.connect(("127.0.0.1", proxy.port))
                start = time.monotonic()
                connection.send_bytes(payload)
                seconds = time.monotonic() - start
            except ConnectionError as exc:
                error = str(exc)
            if connection.sender:
                stats = dict(connection.sender.stats)
        thread.join(timeout=60)
    hung = thread.is_alive()
    recv_sock.close()
    sha_ok = (
        "data" in outcome
        and hashlib.sha256(outcome["data"]).digest() == hashlib.sha256(payload).digest()
    )
    return {
        "ok": sha_ok and not hung and not error,
        "hung": hung,
        "error": error or outcome.get("rx_error", ""),
        "seconds": seconds,
        "mbps": size * 8 / seconds / 1e6 if seconds else 0.0,
        **stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--size-mib", type=float, default=10)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--loss", type=float, nargs="+", default=[0, 2, 5],
                        help="loss percentages, applied in each direction")
    args = parser.parse_args()
    size = int(args.size_mib * 1024 * 1024)
    failures = 0
    for loss in args.loss:
        results = [one_run(size, loss / 100, seed) for seed in range(1, args.runs + 1)]
        good = [r for r in results if r["ok"]]
        failures += len(results) - len(good)
        line = f"loss {loss:>4}%  pass {len(good)}/{len(results)}"
        if good:
            mbps = [r["mbps"] for r in good]
            line += (
                f"  goodput {statistics.mean(mbps):6.1f} +/- "
                f"{statistics.pstdev(mbps):5.1f} Mbps  "
                f"retx {statistics.mean(r['retransmissions'] for r in good):7.1f}  "
                f"timeouts {statistics.mean(r['timeouts'] for r in good):5.1f}"
            )
        print(line, flush=True)
        for r in results:
            if not r["ok"]:
                print("   FAIL:", r, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
