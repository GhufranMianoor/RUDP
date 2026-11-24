"""Send a file over RUDP.

    python apps/send_file.py HOST PORT FILE [--cc {reno,null}] [--log PATH]
                             [--summary PATH] [--window N]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import Connection, ConnectionError
from rudp.constants import DEFAULT_WINDOW
from rudp.controller import NullController, RenoController
from rudp.eventlog import EventLogger


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a file reliably over UDP.")
    parser.add_argument("host", help="Destination IP or hostname")
    parser.add_argument("port", type=int, help="Destination UDP port")
    parser.add_argument("file", help="File to send")
    parser.add_argument("--cc", choices=["reno", "null"], default="reno",
                        help="Congestion controller to use (default: reno)")
    parser.add_argument("--log", type=Path, default=None,
                        help="Optional path to output JSON-lines event log")
    parser.add_argument("--summary", type=Path, default=None,
                        help="Optional path for a one-object JSON summary of the transfer")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"Maximum window / sequence span in packets (default {DEFAULT_WINDOW})")
    parser.add_argument("--progress", action="store_true",
                        help="print cwnd, ssthresh, state, RTO and progress to stderr every 0.5 s")
    parser.add_argument("--init-cwnd", type=float, default=1.0,
                        help="Initial congestion window in packets (default 1)")
    args = parser.parse_args()

    with open(args.file, "rb") as source:
        data = source.read()

    logger = EventLogger(destination=args.log) if args.log else None
    if args.cc == "reno":
        # ssthresh starts at the window cap ("infinite"), so slow start runs
        # until the first loss, as in real TCP.
        controller = RenoController(
            initial_cwnd=args.init_cwnd, initial_ssthresh=float(args.window), logger=logger
        )
    else:
        controller = NullController(window_size=min(args.window, 32), logger=logger)

    status = 0
    error = ""
    started = time.monotonic()
    connection = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        connection = Connection(sock, controller=controller, logger=logger, window_size=args.window)
        try:
            connection.connect((args.host, args.port))
            started = time.monotonic()  # time the transfer, not the handshake
            connection.send_bytes(data, progress=_printer(len(data), started) if args.progress else None)
        except ConnectionError as exc:
            status, error = 1, str(exc)
    elapsed = time.monotonic() - started

    summary = {
        "ok": status == 0,
        "error": error,
        "bytes": len(data),
        "seconds": elapsed,
        "goodput_mbps": (len(data) * 8 / elapsed / 1e6) if status == 0 and elapsed > 0 else 0.0,
        "cc": args.cc,
        **(connection.sender.stats if connection and connection.sender else {}),
    }
    if connection and connection.sender:
        sampler = connection.sender.rtt_sampler.stats
        summary.update(
            samples=sampler.sample_count,
            srtt=sampler.smoothed_rtt,
            min_rtt=sampler.min_rtt,
            mean_rtt=sampler.mean_rtt,
        )
    if args.summary:
        args.summary.write_text(json.dumps(summary) + "\n")
    print(json.dumps(summary))
    if logger is not None:
        logger.close()
    return status


def _printer(total: int, started: float):
    def show(sender) -> None:
        controller = sender.controller
        delivered = min(total, (sender.base - 1) * 1200)
        state = getattr(controller, "state", "")
        print(
            f"t={time.monotonic() - started:6.1f}s  cwnd={controller.window():5d}  "
            f"ssthresh={getattr(controller, 'ssthresh', 0):7.1f}  "
            f"state={getattr(state, 'value', state):<20}  rto={controller.rto() * 1000:6.0f} ms  "
            f"done={100 * delivered / max(1, total):5.1f}%  retx={sender.stats['retransmissions']}",
            file=sys.stderr, flush=True,
        )
    return show


if __name__ == "__main__":
    raise SystemExit(main())
