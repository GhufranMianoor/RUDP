"""Send a file over RUDP: python apps/send_file.py HOST PORT FILE [--cc {reno,null}] [--log PATH]."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import Connection
from rudp.controller import NullController, RenoController
from rudp.eventlog import EventLogger


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a file reliably over UDP.")
    parser.add_argument("host", help="Destination IP or hostname")
    parser.add_argument("port", type=int, help="Destination UDP port")
    parser.add_argument("file", help="File to send")
    parser.add_argument(
        "--cc",
        choices=["reno", "null"],
        default="reno",
        help="Congestion controller to use (default: reno)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Optional path to output JSON-lines congestion/event log",
    )
    args = parser.parse_args()

    with open(args.file, "rb") as source:
        data = source.read()

    logger = EventLogger(destination=args.log) if args.log else None

    if args.cc == "reno":
        controller = RenoController(initial_cwnd=1.0, initial_ssthresh=64.0, logger=logger)
    else:
        controller = NullController(logger=logger)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        connection = Connection(sock, controller=controller)
        connection.connect((args.host, args.port))
        connection.send_bytes(data)

    if logger is not None:
        logger.close()


if __name__ == "__main__":
    main()
