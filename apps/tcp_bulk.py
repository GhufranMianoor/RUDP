#!/usr/bin/env python3
"""TCP bulk transfer with an exact, receiver-side completion time.

Used by the experiment runner for the TCP Reno / CUBIC baselines instead of
``iperf3 -n``. ``iperf3 -n N`` ends the test as soon as the *sender* has
written N bytes into its socket; on a slow bottleneck most of those bytes are
still in the send buffer or the queue, so the server reports only part of them
(and a meaningless rate). Here the receiver reads until the sender closes, then
reports the bytes and the time from accept to the last byte, the same
definition of goodput RUDP uses (all bytes delivered / time).

    tcp_bulk.py server PORT [--interval 0.5]
    tcp_bulk.py client HOST PORT BYTES [--cc reno|cubic]

The server accepts one connection, prints a JSON object with 0.5 s receive
intervals (``{"intervals": [{"sum": {"end": s, "bytes": b}}, ...]}``, the same
shape as iperf3's server output) and exits. The client prints::

    {"end": {"sum_sent": {"bytes": .., "retransmits": ..},
             "sum_received": {"bytes": .., "seconds": .., "bits_per_second": ..}},
     "tcp": {"cc": "reno", "rtt_ms": .., "snd_cwnd": ..}}
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time

CHUNK = 64 * 1024


def tcp_info(sock: socket.socket) -> dict:
    """A few fields of Linux ``struct tcp_info`` (empty off Linux)."""

    try:
        raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    except (AttributeError, OSError):
        return {}
    if len(raw) < 104:
        return {}
    # 8 x u8, then u32 fields: rto ato snd_mss rcv_mss unacked sacked lost
    # retrans fackets last_data_sent last_ack_sent last_data_recv last_ack_recv
    # pmtu rcv_ssthresh rtt rttvar snd_ssthresh snd_cwnd advmss reordering
    # rcv_rtt rcv_space total_retrans
    fields = struct.unpack_from("24I", raw, 8)
    return {"rtt_ms": fields[15] / 1000.0, "snd_cwnd": fields[18],
            "total_retrans": fields[23]}


def serve(port: int, interval: float) -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen(1)
    conn, _ = listener.accept()
    listener.close()
    start = time.monotonic()
    total = 0
    intervals: list[dict] = []
    bucket_end, bucket_bytes = interval, 0
    seconds = 0.0
    while True:
        data = conn.recv(1 << 20)
        if not data:
            break
        elapsed = time.monotonic() - start
        while elapsed >= bucket_end:     # close every finished interval
            intervals.append({"sum": {"start": bucket_end - interval, "end": bucket_end,
                                      "bytes": bucket_bytes}})
            bucket_end, bucket_bytes = bucket_end + interval, 0
        bucket_bytes += len(data)
        total += len(data)
        seconds = elapsed                # time of the last byte
    if bucket_bytes:
        intervals.append({"sum": {"start": bucket_end - interval, "end": seconds,
                                  "bytes": bucket_bytes}})
    summary = {"bytes": total, "seconds": seconds,
               "bits_per_second": total * 8 / seconds if seconds > 0 else 0.0}
    try:
        conn.sendall((json.dumps(summary) + "\n").encode())
    except OSError:
        pass
    conn.close()
    print(json.dumps({"intervals": intervals, "end": {"sum_received": summary}}), flush=True)
    return 0


def client(host: str, port: int, nbytes: int, cc: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if cc:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, cc.encode())
    for attempt in range(50):            # the receiver may still be starting
        try:
            sock.connect((host, port))
            break
        except ConnectionRefusedError:
            if attempt == 49:
                raise
            time.sleep(0.1)
    block = b"\x00" * CHUNK
    left = nbytes
    while left > 0:
        sent = sock.send(block[:min(CHUNK, left)])
        left -= sent
    sock.shutdown(socket.SHUT_WR)          # FIN after the last byte
    reply = b""
    while not reply.endswith(b"\n"):
        data = sock.recv(4096)
        if not data:
            break
        reply += data
    info = tcp_info(sock)
    sock.close()
    try:
        received = json.loads(reply)
    except json.JSONDecodeError:
        print(json.dumps({"error": "server sent no summary"}), flush=True)
        return 1
    used = cc or "default"
    print(json.dumps({
        "end": {"sum_sent": {"bytes": nbytes, "retransmits": info.get("total_retrans", "")},
                "sum_received": received},
        "tcp": {"cc": used, **{k: v for k, v in info.items() if k != "total_retrans"}},
    }), flush=True)
    return 0 if received.get("bytes") == nbytes else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("server")
    s.add_argument("port", type=int)
    s.add_argument("--interval", type=float, default=0.5)
    c = sub.add_parser("client")
    c.add_argument("host")
    c.add_argument("port", type=int)
    c.add_argument("bytes", type=int)
    c.add_argument("--cc", default="", help="TCP congestion control, e.g. reno or cubic")
    args = parser.parse_args()
    if args.mode == "server":
        return serve(args.port, args.interval)
    return client(args.host, args.port, args.bytes, args.cc)


if __name__ == "__main__":
    sys.exit(main())
