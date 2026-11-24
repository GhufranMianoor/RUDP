# RUDP

Reliable transport over UDP for CS3001: selective repeat with CRC32, SACK,
receiver flow control, Reno and CUBIC congestion control, RTT/RTO estimation (Karn), a
routed Mininet topology, and experiments against TCP Reno and CUBIC.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for subnets, addresses, ports and
flows, [docs/congestion_controller_design.md](docs/congestion_controller_design.md)
for the loss-recovery design, and [docs/STATUS.md](docs/STATUS.md) for what has and
has not been verified.

## Setup (fresh Ubuntu 22.04 / 24.04)

```bash
git clone https://github.com/GhufranMianoor/RUDP.git
cd RUDP
sudo scripts/setup_ubuntu.sh        # apt packages + .venv (with system site-packages for Mininet)
.venv/bin/python -m pytest -q       # 87 tests, ~45 s, no root needed
```

## Quick local transfer (no Mininet)

```bash
python apps/recv_file.py 9000 received.bin &
python apps/send_file.py 127.0.0.1 9000 input.bin --log results/local.jsonl
sha256sum input.bin received.bin
```

`send_file.py` prints a one-line JSON summary (goodput, retransmissions, timeouts,
RTT) and exits 1 if the transfer fails. `recv_file.py` exits 0 after a clean FIN
exchange and 1 if the sender disappears (`--idle-timeout`, default 30 s), so
scripts never hang.

Stress the protocol on localhost through the impairment proxy:

```bash
python tools/loss_soak.py --size-mib 10 --runs 10 --loss 0 2 5   # every SHA must match
python tools/lossy_proxy.py 9001 127.0.0.1 9000 --drop 0.05      # manual proxy
```

`send_file.py --cc cubic` uses the CUBIC controller (RFC 8312/9438) instead of
Reno (the default). Add `--progress` to `send_file.py` to print cwnd, ssthresh, state, RTO and progress
every 0.5 s.

## Live POC demo

```bash
sudo .venv/bin/python tools/live_demo.py --pause            # Enter between steps
sudo .venv/bin/python tools/live_demo.py --capture --cli    # also save a pcapng, open the Mininet CLI
```

Shows the interfaces and routing tables of h1, h2, R1 and R2 and the bottleneck
qdiscs, runs traceroute h1 -> h2, then sends a 30 MiB file while printing cwnd every
0.5 s. The network changes during the transfer (default `--phases
"0:25ms:0%,10:25ms:2%,20:100ms:0%"`: RTT 50 ms, then 2% loss from 10 s, then RTT
200 ms without loss from 20 s). It ends with the SHA-256 of both files and
`results/demo/cwnd_demo.png` with the phase changes marked.

## Routed topology

```bash
sudo .venv/bin/python topology/topo.py --delay 25ms --loss 0% --rate 10mbit --check
sudo .venv/bin/python topology/topo.py --delay 50ms --loss 2% --rate 10mbit   # interactive CLI
```

`--delay` is one-way on each bottleneck egress (RTT = 2 x delay); `--loss` is per
direction. `--check` verifies addressing, traceroute (`10.0.1.1`, `10.0.2.2`,
`10.0.3.10`), RTT, iperf3 rate and an end-to-end RUDP SHA-256. If a run crashes
and leaves interfaces behind, run `sudo mn -c`.

## Experiments

```bash
sudo .venv/bin/python experiments/run_experiments.py --list     # show the matrix, run nothing
sudo scripts/run_all.sh                                          # check -> matrix -> captures -> plots
sudo .venv/bin/python experiments/run_experiments.py --only Exp1 --runs 3   # one experiment
```

| Experiment | Conditions (others held at RTT 100 ms, 10 Mbit/s, 0% loss) |
|---|---|
| Exp1 | loss 0%, 2%, 5% (per direction) |
| Exp2 | RTT 20, 100, 200 ms |
| Exp3 | bottleneck 5, 10, 20 Mbit/s |
| Exp4 | RUDP (h1->h2) and iperf3 (h3->h4) **concurrently** on 10 Mbit/s: `fairness_reno` against TCP Reno, `fairness_cubic` against TCP CUBIC |

Each condition builds a fresh network; every run first pings h1 -> h2 on the idle
path (20 probes, the independently measured RTT), then does RUDP, then iperf3 Reno,
then iperf3 CUBIC (Exp4: the two concurrent flows). RUDP uses its Reno controller;
add `--rudp-cc reno cubic` to also run RUDP with the CUBIC controller in Exp1-3
(rows labelled `RUDP-CUBIC`, about a third more run time). Default 3 runs and a 10 MiB payload
(`--payload-mib`). **Expect hours at 5% loss**: Reno at 100 ms RTT and 5% loss
delivers well under 1 Mbit/s, so a 10 MiB transfer takes minutes per run.

Output in `results/`: `raw.csv` (one row per transfer), `summary.csv` (mean and
standard deviation over successful runs), `logs/*.jsonl` (RUDP event logs, every run;
logging is buffered and does not slow the sender), `pcaps/` (first run of each condition, captured in R1), `plots/`,
and `report_tables.md` (Markdown tables for the report).

How the numbers are defined:

- **RUDP goodput** = payload bytes / sender-measured time from the end of the
  handshake to the FIN being acknowledged; the SHA-256 of the received file must match.
- **TCP goodput** = iperf3 `sum_received` (receiver side). With `-n`, iperf3 stops when the
  sender has written the last byte, so data still queued at the bottleneck (about
  0.3 MB) is missing from `sum_received`: TCP is under-counted by up to about 3% at
  10 MiB. Do not use smaller payloads for TCP comparisons.
- **Exp4 fairness**: both flows send the same number of bytes starting together. Each
  flow's throughput is measured over the same window, from when both deliver data to
  when the first finishes (RUDP from its event log, TCP from the iperf3 receiver's
  0.5 s intervals): `overlap_mbps`, `share_of_total` and `fairness_index` (Jain). The
  older per-flow-duration index, which over-states fairness, is kept as
  `fairness_index_own_duration` for comparison only.
- **Measured RTT** (`ping_avg_rtt_ms`, `ping_min_rtt_ms`): `ping -c 20` from h1 to h2
  before each run, on the idle path. Exp2 compares it with RUDP's smoothed RTT
  (`rtt_estimate_s`) and minimum sample (`min_rtt_s`); see
  `plots/rtt_estimate_vs_measured.png` and the extra Exp2 table in `report_tables.md`.
  With loss, some probes are lost; `ping_loss_pct` records how many.
- **Queueing delay** (`queue_delay_ms`) = RUDP smoothed RTT minus the minimum RTT seen
  in the run; with no loss it is mostly the bottleneck queue.
- A failed transfer (hash mismatch, timeout) is kept in `raw.csv` with `ok=False` and
  excluded from the means; `summary.csv` shows `runs_ok/runs`.

## Packet-capture evidence

```bash
sudo .venv/bin/python tools/capture_evidence.py     # handshake, loss-recovery, window-growth, teardown, flow-control
```

Writes five `.pcapng` files to `results/pcaps/`, the matching RUDP logs to
`results/logs/`, and decoded text to `results/evidence/` when tshark can run Lua.
Load the dissector in Wireshark with **Analyze > Lua Plugins** or
`wireshark -X lua_script:tools/rudp_dissector.lua results/pcaps/loss-recovery.pcapng`.
Useful filters: `rudpcn`, `rudpcn.flags.syn == 1`, `rudpcn.sack_count > 0`,
`rudpcn.flags.fin == 1`. The Info column shows e.g. `ACK ack=235 win=1023 SACK[236-236,238-238]`.
SACK ranges are **inclusive**. Wireshark refuses Lua when run as root: open captures as your
normal user.

Annotated screenshots go in `results/evidence/`; [docs/SCREENSHOT_CHECKLIST.md](docs/SCREENSHOT_CHECKLIST.md)
lists the filter and the labels for each one (frame numbers, addresses and ports,
flags, sequence/ack, window, SACK ranges and CRC32).

## Layout

```
rudp/        protocol (packet, sender, receiver, controller, rtt, connection)
apps/        send_file.py, recv_file.py
topology/    topo.py (Mininet; library + CLI)
experiments/ run_experiments.py, fairness.py
tools/       live_demo.py, capture_evidence.py, rudp_dissector.lua, lossy_proxy.py, loss_soak.py
analysis/    plot_results.py, make_report_tables.py
tests/       unit + localhost impairment tests
scripts/     setup_ubuntu.sh, run_all.sh
```
