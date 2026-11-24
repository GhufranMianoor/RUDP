"""Receive a file over RUDP: python apps/recv_file.py PORT FILE."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.connection import Connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("file")
    args = parser.parse_args()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", args.port))
        connection = Connection(sock)
        data = connection.receive_bytes()
    with open(args.file, "wb") as destination:
        destination.write(data)


if __name__ == "__main__":
    main()
