"""Receive a file over RUDP: python apps/recv_file.py PORT FILE [--idle-timeout S].

Exits 0 after a clean FIN handshake and 1 if the sender disappears
(no packet for --idle-timeout seconds), so scripts never hang.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import DEFAULT_IDLE_TIMEOUT, Connection, ConnectionError
from rudp.constants import DEFAULT_WINDOW


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("file")
    parser.add_argument("--bind", default="0.0.0.0", help="Address to bind (default 0.0.0.0)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT,
                        help="Give up after this many seconds without a packet (0 = never)")
    args = parser.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.bind, args.port))
        connection = Connection(
            sock, window_size=args.window, idle_timeout=args.idle_timeout or None
        )
        try:
            data = connection.receive_bytes()
        except ConnectionError as exc:
            print(f"recv_file: {exc}", file=sys.stderr)
            return 1
    with open(args.file, "wb") as destination:
        destination.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
