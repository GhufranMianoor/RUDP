"""A simple UDP proxy for testing loss, duplication, delay, and corruption."""

from __future__ import annotations

import argparse
import random
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("listen_port", type=int)
    parser.add_argument("target_host")
    parser.add_argument("target_port", type=int)
    parser.add_argument("--drop", type=float, default=0)
    parser.add_argument("--duplicate", type=float, default=0)
    parser.add_argument("--corrupt", type=float, default=0)
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    if any(not 0 <= value <= 1 for value in (args.drop, args.duplicate, args.corrupt)):
        parser.error("drop, duplicate, and corrupt must be between 0 and 1")
    target = (args.target_host, args.target_port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("0.0.0.0", args.listen_port))
        client: tuple[str, int] | None = None
        while True:
            data, address = listener.recvfrom(65535)
            if address == target:
                if client is None:
                    continue
                destination = client
            else:
                client = address
                destination = target
            if random.random() < args.drop:
                continue
            if random.random() < args.corrupt and data:
                data = data[: len(data) // 2] + bytes([data[len(data) // 2] ^ 1]) + data[len(data) // 2 + 1 :]
            if args.delay:
                time.sleep(args.delay)
            listener.sendto(data, destination)
            if random.random() < args.duplicate:
                listener.sendto(data, destination)
