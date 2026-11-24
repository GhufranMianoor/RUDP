# Status

Last updated after the protocol, topology and runner fixes. "Verified" names the
environment, because the sandbox used for development has no `netem` and no OVS
kernel module.

## Verified (and where)

| Item | Evidence |
|---|---|
| Protocol, unit + localhost impairment tests | 89 tests pass: SACK cap and ordering, >3 gaps, SYN / SYN+ACK / FIN / FIN+ACK loss, duplication, reordering, corruption, 20% loss, idle and stall time-outs, zero window, once-per-window Reno response, one response per timeout episode, new loss event during SACK recovery, overlap-window fairness, CUBIC controller (beta, fast convergence, K, concave/convex growth, TCP-friendly region, RTO), CUBIC transfers at 5% loss, ping parsing, summary/plots with ping RTT and CUBIC fairness |
| Protocol under loss | `tools/loss_soak.py`, 10 MiB x 10 runs at 0/2/5% loss through the proxy: 30/30 SHA-256 matches, no hangs, receiver always exits |
| Topology | Real Mininet 2.3 + Open vSwitch (userspace datapath), real `tbf`: ping h1->h2 and h3->h4, traceroute `10.0.1.1 -> 10.0.2.2 -> 10.0.3.10`, iperf3 TCP 94% of 10 Mbit/s, RUDP h1->h2 across both routers with matching SHA-256 |
| Experiment runner | Real Mininet, Exp3 + Exp4, 1 run, 2 MiB: fresh network per condition, SHA-checked RUDP, iperf3 Reno/CUBIC, concurrent Exp4, CSV output, tcpdump capture inside R1 |
| Evidence tool | Four pcapng traces produced inside Mininet; handshake, FIN/FIN+ACK, SACK recovery and retransmissions confirmed in the decoded output |
| Wireshark dissector | tshark 4.2.2 as a non-root user decodes the generated traces |
| Version 2.2 additions (no Mininet available) | Buffered event log: logged localhost run 158 Mbit/s vs 176 unlogged (was 34). Exp4 overlap fairness and the queueing-delay column exercised end to end with a mocked network and a fake iperf3. `tools/live_demo.py` exercised with a mocked network (transfer, phase changes, SHA-256, cwnd plot). Flow-control capture settings (window 16, 1% loss) tested on localhost through the lossy proxy. |

| Lab VM (Ubuntu, VirtualBox), 2026-11-24 | 87 tests pass. `topo.py --check` passes at 25 ms / 0%: routes, traceroute, RTT, RUDP SHA-256. At 25 ms / 2% netem and tbf are attached, RTT 56.8 ms and RUDP passes; iperf3 reached 2.08 Mbit/s, which the old 30%-of-rate threshold failed although it is what loss-limited TCP should get (Mathis model: ~2.0 Mbit/s); the check now uses that model. `tools/live_demo.py --pause --capture --cli` ran end to end and produced the cwnd plot. Trial Exp3 + Exp4 (1 run, 2 MiB): every RUDP transfer ok; 10 of 11 TCP runs "failed" with 12-87% of the bytes, caused by `iperf3 -n` stopping when the sender has written the bytes; TCP now uses `apps/tcp_bulk.py`, which waits for the receiver to get every byte. The demo's cwnd stayed under 50 packets after the 200 ms phase (spurious timeouts, RTO only ~1 ms above SRTT); fixed with a 0.2 s RTO margin. |

## NOT verified yet - must be done on the Ubuntu lab machine

- **Re-run the trial** (`--only Exp3 Exp4 --runs 1 --payload-mib 2`) with `apps/tcp_bulk.py`:
  every TCP row should now say `ok`. Re-run the live demo: after the 200 ms phase cwnd
  should climb well above 50 packets.
- **Kernel-datapath OVS.** Only the userspace datapath was exercised.
- **The full matrix**: 3 conditions x 3 runs x 10 MiB for every experiment. Only Exp3/Exp4
  with 2 MiB and one run were executed, and without delay or loss.
- **Reproduction on a fresh Ubuntu VM** following only the README.
- **Wireshark GUI** annotated screenshots (`results/evidence/`), following
  `docs/SCREENSHOT_CHECKLIST.md`.
- **`tools/live_demo.py` in real Mininet**, including `tc qdisc change` of netem while
  tbf stays attached as its child. Rehearse it once before the live demo.
- **Version 2.3 additions in Mininet**: the per-run ping (`ping_avg_rtt_ms` in `raw.csv`),
  Exp4 at three RTTs against TCP Reno and TCP CUBIC (6 conditions), and `--rudp-cc reno cubic`. Tested only with unit tests and a
  localhost run through the lossy proxy (4 MiB, 50 ms RTT, 1% loss: Reno and CUBIC
  both SHA-256 correct).
- **The fifth capture** (`flow-control`) and the Exp4 overlap fairness with real TCP.

- **Architecture figure**: `docs/figures/architecture.svg/.png` is done and matches the proposal's addressing plan; check it against what you actually demo.

## Results and report

- Full matrix done on the lab VM: 3 runs per condition, 2 MiB per transfer, 117/117 transfers
  verified (`results/full/`, tables in `results/full/report_tables.md`, plots in `results/full/plots/`).
- Routed evidence traces in `results/pcaps/`; final report in `REPORT.md` and `docs/RUDP_Technical_Report.pdf`.

## Known limitations

- RTT is sampled from sender-local send times (Karn); the timestamp-echo field is on the
  wire but not used for sampling. Packets acknowledged cumulatively after a hole fills,
  and not SACKed earlier, can give inflated samples.
- Loss is applied per direction, so ACKs are also lost.
