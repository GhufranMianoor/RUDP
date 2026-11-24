"""Fairness over the window in which both flows are actually running.

Exp4 starts RUDP (h1 -> h2) and iperf3 TCP (h3 -> h4) together, each sending
the same number of bytes. Taking each flow's goodput over its *own* duration
inflates Jain's index: once one flow finishes, the other has the link to
itself. Here both flows are measured over the same overlap window instead.

* RUDP: cumulative delivered bytes over time, from the sender's ``ack``
  events (one per newly acknowledged packet) in its event log.
* TCP: cumulative received bytes over time, from the iperf3 *server*
  (receiver) JSON ``intervals`` (``-i 0.5``), interpolated inside an interval.

Both processes run on the same kernel, so ``time.monotonic()`` in the runner
and in the RUDP sender share a clock. The iperf3 timeline is anchored at the
moment the runner launched the client; connection set-up makes that anchor up
to about one RTT early, which is small against a multi-second window.
"""

from __future__ import annotations

from bisect import bisect_right
import json
from pathlib import Path


def rudp_delivery_curve(log_path: Path, packet_bytes: int = 1200) -> list[tuple[float, int]]:
    """``[(monotonic_time, cumulative_bytes)]`` from a RUDP sender event log."""

    curve: list[tuple[float, int]] = []
    total = 0
    with Path(log_path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "ack":
                continue
            total += packet_bytes
            curve.append((float(event["monotonic"]), total))
    curve.sort()
    return curve


def iperf_delivery_curve(server_json: str | dict, started: float) -> list[tuple[float, int]]:
    """``[(monotonic_time, cumulative_bytes)]`` from iperf3 server JSON output."""

    data = json.loads(server_json) if isinstance(server_json, str) else server_json
    curve: list[tuple[float, int]] = [(started, 0)]
    total = 0
    for interval in data.get("intervals", []):
        summary = interval.get("sum", {})
        if "end" not in summary or "bytes" not in summary:
            continue
        total += int(summary["bytes"])
        curve.append((started + float(summary["end"]), total))
    return curve


def bytes_at(curve: list[tuple[float, int]], when: float) -> float:
    """Cumulative bytes at ``when``, linearly interpolated between points."""

    if not curve or when <= curve[0][0]:
        return 0.0
    if when >= curve[-1][0]:
        return float(curve[-1][1])
    index = bisect_right([t for t, _ in curve], when)
    (t0, b0), (t1, b1) = curve[index - 1], curve[index]
    if t1 == t0:
        return float(b1)
    return b0 + (b1 - b0) * (when - t0) / (t1 - t0)


def overlap_throughputs(
    a: list[tuple[float, int]], b: list[tuple[float, int]]
) -> tuple[float, float, float] | None:
    """Mbit/s of each flow over the window in which both are delivering.

    Returns ``(a_mbps, b_mbps, window_seconds)`` or ``None`` without overlap.
    """

    if len(a) < 2 or len(b) < 2:
        return None
    start = max(a[0][0], b[0][0])
    end = min(a[-1][0], b[-1][0])
    window = end - start
    if window <= 0:
        return None
    rate_a = (bytes_at(a, end) - bytes_at(a, start)) * 8 / window / 1e6
    rate_b = (bytes_at(b, end) - bytes_at(b, start)) * 8 / window / 1e6
    return rate_a, rate_b, window


def jains_fairness(values: list[float]) -> float:
    if not values or not any(values):
        return 0.0
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))
