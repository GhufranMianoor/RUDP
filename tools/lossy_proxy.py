"""UDP impairment proxy for localhost tests: loss, duplication, reordering,
corruption and delay, with per-direction control and a packet-level hook.

Command line (the proxy used to exit immediately because ``main()`` was never
called; it now runs until interrupted)::

    python tools/lossy_proxy.py LISTEN_PORT TARGET_HOST TARGET_PORT --drop 0.02

Library use (see tests/test_impairments.py)::

    with LossyProxy(("127.0.0.1", port), drop=0.05, seed=1) as proxy:
        ...send to ("127.0.0.1", proxy.port)...

``drop`` applies to both directions unless ``drop_forward`` / ``drop_reverse``
override it. ``hook(direction, data) -> bool`` may return True to drop a
specific packet (used to kill exactly the SYN, SYN+ACK, FIN or FIN+ACK).
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import random
import socket
import threading
import time
from typing import Callable

FORWARD = "forward"  # client -> target
REVERSE = "reverse"  # target -> client


class LossyProxy:
    def __init__(
        self,
        target: tuple[str, int],
        *,
        listen: tuple[str, int] = ("127.0.0.1", 0),
        drop: float = 0.0,
        drop_forward: float | None = None,
        drop_reverse: float | None = None,
        duplicate: float = 0.0,
        corrupt: float = 0.0,
        reorder: float = 0.0,
        reorder_delay: float = 0.02,
        delay: float = 0.0,
        seed: int | None = None,
        hook: Callable[[str, bytes], bool] | None = None,
    ) -> None:
        for name, value in (
            ("drop", drop), ("duplicate", duplicate), ("corrupt", corrupt), ("reorder", reorder),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        self.target = target
        self.drop = {
            FORWARD: drop if drop_forward is None else drop_forward,
            REVERSE: drop if drop_reverse is None else drop_reverse,
        }
        self.duplicate = duplicate
        self.corrupt = corrupt
        self.reorder = reorder
        self.reorder_delay = reorder_delay
        self.delay = delay
        self.hook = hook
        self.rng = random.Random(seed)
        self.stats = {"forwarded": 0, "dropped": 0, "duplicated": 0, "corrupted": 0, "reordered": 0}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(listen)
        self.port = self._sock.getsockname()[1]
        self._client: tuple[str, int] | None = None
        self._queue: list[tuple[float, int, bytes, tuple[str, int]]] = []
        self._counter = itertools.count()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------- lifecycle

    def start(self) -> "LossyProxy":
        self._thread = threading.Thread(target=self.serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._sock.close()

    def __enter__(self) -> "LossyProxy":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------- engine

    def serve(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            now = time.monotonic()
            while self._queue and self._queue[0][0] <= now:
                _, _, data, destination = heapq.heappop(self._queue)
                self._send(data, destination)
            timeout = 0.05
            if self._queue:
                timeout = max(0.0005, min(timeout, self._queue[0][0] - now))
            sock.settimeout(timeout)
            try:
                data, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self._handle(data, address)

    def _send(self, data: bytes, destination: tuple[str, int]) -> None:
        try:
            self._sock.sendto(data, destination)
        except OSError:
            pass

    def _handle(self, data: bytes, address: tuple[str, int]) -> None:
        if address == self.target:
            if self._client is None:
                return
            direction, destination = REVERSE, self._client
        else:
            self._client = address
            direction, destination = FORWARD, self.target

        if self.hook is not None and self.hook(direction, data):
            self.stats["dropped"] += 1
            return
        if self.rng.random() < self.drop[direction]:
            self.stats["dropped"] += 1
            return
        if self.rng.random() < self.corrupt and data:
            middle = len(data) // 2
            data = data[:middle] + bytes([data[middle] ^ 1]) + data[middle + 1 :]
            self.stats["corrupted"] += 1

        extra = 0.0
        if self.reorder and self.rng.random() < self.reorder:
            extra = self.reorder_delay
            self.stats["reordered"] += 1
        copies = 1
        if self.rng.random() < self.duplicate:
            copies = 2
            self.stats["duplicated"] += 1
        for _ in range(copies):
            self.stats["forwarded"] += 1
            wait = self.delay + extra
            if wait <= 0:
                self._send(data, destination)
            else:
                heapq.heappush(
                    self._queue,
                    (time.monotonic() + wait, next(self._counter), data, destination),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("listen_port", type=int)
    parser.add_argument("target_host")
    parser.add_argument("target_port", type=int)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--drop", type=float, default=0, help="loss probability, each direction")
    parser.add_argument("--duplicate", type=float, default=0)
    parser.add_argument("--corrupt", type=float, default=0)
    parser.add_argument("--reorder", type=float, default=0)
    parser.add_argument("--delay", type=float, default=0, help="one-way delay in seconds")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    try:
        proxy = LossyProxy(
            (args.target_host, args.target_port),
            listen=(args.listen_host, args.listen_port),
            drop=args.drop,
            duplicate=args.duplicate,
            corrupt=args.corrupt,
            reorder=args.reorder,
            delay=args.delay,
            seed=args.seed,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"lossy_proxy: :{proxy.port} -> {args.target_host}:{args.target_port}", flush=True)
    try:
        proxy.serve()
    except KeyboardInterrupt:
        print(f"\nlossy_proxy stats: {proxy.stats}")


if __name__ == "__main__":
    main()
