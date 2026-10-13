"""Send a file over RUDP: python apps/send_file.py HOST PORT FILE."""

from __future__ import annotations

import argparse
import socket

from rudp.connection import Connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("file")
    args = parser.parse_args()
    with open(args.file, "rb") as source:
        data = source.read()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        connection = Connection(sock)
        connection.connect((args.host, args.port))
        connection.send_bytes(data)


if __name__ == "__main__":
    main()
