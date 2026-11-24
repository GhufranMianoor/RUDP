# Reliable UDP (RUDP) Technical Report

**Repository:** `GhufranMianoor/RUDP`
**Report date:** 2026-11-21

## 1. Objective

This project implements a reliable, connection-oriented transport over UDP and
evaluates it against TCP Reno and TCP CUBIC. The POC is intended to demonstrate
connection setup and teardown, sequence-based data transfer, acknowledgements,
selective acknowledgements (SACK), retransmission, congestion control, RTT
estimation, flow control, and integrity validation.

## 2. System architecture

The experimental topology uses two Linux routers and four end hosts. The
left hosts use `10.0.1.0/24`, the router WAN uses `10.0.2.0/24`, and the right
hosts use `10.0.3.0/24`. Full interface addresses and static routes are listed
in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). RUDP data uses UDP 9000;
iperf3 uses its own TCP/UDP control/data ports.

Linux IP forwarding and static routes provide end-to-end reachability.
`tc netem` injects delay and packet loss and a TBF queue creates bottleneck
bandwidth. The routers therefore act as measurable impairment points rather
than as switches only.

## 3. RUDP protocol design

Every RUDP datagram begins with a 32-byte, big-endian header:

```text
>BBHIIHHIIIB3x
```

It contains version, flags, connection ID, sequence number, cumulative
acknowledgement, receive window, payload length, timestamp, timestamp echo,
CRC32, SACK block count, and reserved bytes. Up to three 8-byte inclusive
SACK ranges follow the fixed header. Payloads are limited to 1200 bytes.

Flags are SYN, ACK, FIN, DATA, and RST. Sequence numbers identify bytes or
logical packet units; acknowledgements are cumulative. The 32-bit CRC32
protects the header, SACK blocks, and payload.

## 4. Reliability and congestion control

The sender maintains a sliding window and retransmission queue. It supports
selective-repeat delivery and effective flow-control windows from receiver
ACK packets. The connection class retransmits handshake SYN packets, sends
SYN+ACK responses, validates peer addresses and connection IDs, and has FIN
linger for teardown.

The Reno-style controller implements slow start, congestion avoidance,
duplicate-ACK loss response, timeout loss response, and recovery state.
SACK-driven acknowledgement processing reports gaps while the receiver tracks
out-of-order packets and emits a cumulative ACK plus SACK ranges.

RTT samples use timestamp echo values. Karn's algorithm prevents retransmitted
packets from producing ambiguous RTT samples. The RTO estimator uses smoothed
RTT, RTT variation, and bounded minimum/maximum RTO values.

## 5. POC implementation

`apps/recv_file.py` binds UDP 9000 and writes the reassembled file.
`apps/send_file.py` reads a file, opens a RUDP connection, sends the data,
and optionally writes a JSON-lines congestion/event log.
`topology/topo.py` creates the routed Mininet network and validates routes and
SHA-256 file integrity. `rudp_dissector.lua` exposes RUDP fields in Wireshark.

A live POC is demonstrated by:

```bash
sudo .venv/bin/python topology/topo.py --delay 25ms --loss 0% --rate 10mbit --check
```

The check prints routes and traceroute output, transfers the README by RUDP,
and compares the sender and receiver SHA-256 values.

## 6. Packet capture evidence

Capture RUDP traffic on R1's WAN interface:

```bash
sudo tools/capture_runs.sh R1-eth3 results/pcaps 30
```

Load [tools/rudp_dissector.lua](tools/rudp_dissector.lua) in Wireshark with
**Analyze > Lua Plugins**. Recommended evidence is:

1. SYN / SYN+ACK / ACK handshake
2. DATA, ACK, and SACK loss recovery
3. DATA bursts showing window growth
4. FIN / ACK teardown

Annotate screenshots with frame number, source/destination, UDP ports,
RUDP flags, connection ID, sequence, acknowledgement, window, SACK ranges,
timestamp fields, and CRC32.

## 7. Experimental method

The experiment runner executes RUDP Reno plus TCP Reno and TCP CUBIC for three
runs under:

| Experiment | Condition |
|---|---|
| Exp1 | 0%, 2%, 5% loss |
| Exp2 | 20 ms, 100 ms, 200 ms RTT |
| Exp3 | 5 Mbps, 10 Mbps, 20 Mbps bottleneck |
| Exp4 | 10 Mbps fairness condition |

Measured metrics include completion time, goodput, retransmission count,
estimated RTT, payload SHA-256, and Jain's fairness index. The 10 MiB payload
is deterministic and hashed for integrity.

The complete matrix runs with:

```bash
sudo scripts/run_all.sh
```

`results/raw.csv` contains each run; `results/summary.csv` contains per-condition
means and standard deviations/ranges. The plot script produces goodput,
congestion-window, RTT, and fairness graphs.

## 8. Reproduction procedure

```bash
git clone https://github.com/GhufranMianoor/RUDP.git
cd RUDP
sudo scripts/setup_ubuntu.sh
source .venv/bin/activate
python -m pytest -q
sudo scripts/run_all.sh
```

For a dependency-free matrix schema check:

```bash
python experiments/run_experiments.py --dry-run --runs 3 --results /tmp/rudp-results
python analysis/plot_results.py --results /tmp/rudp-results --output /tmp/rudp-plots
```

## 9. Validation and limitations

The unit tests cover packet encoding/decoding, sender and receiver state,
RTT, congestion-control transitions, and transfer behavior. Mininet, Linux
`tc`, dumpcap, and iperf3 require a root Ubuntu environment and may not work
inside ordinary containers or CI runners.

Packet loss and RTT experiments require the routed Mininet topology; changing
conditions while a transfer is active may invalidate that run. For repeatable
measurements, run each condition from a fresh topology and keep CPU load
constant.

## 10. Deliverables

- Source POC: `rudp/`, `apps/`
- Topology: `topology/topo.py`
- Experiments: `experiments/run_experiments.py`
- Capture scripts: `tools/capture_runs.sh`
- Wireshark dissector: `tools/rudp_dissector.lua`
- Reproducibility: `README.md`, `scripts/setup_ubuntu.sh`, `scripts/run_all.sh`
- Architecture: `docs/ARCHITECTURE.md`
- Test suite: `tests/`
