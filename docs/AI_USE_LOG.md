# AI use log

Record every use of an AI tool on this project: the tool, what it was asked, what it
produced, and how the team checked it. The team must add its own earlier and later
entries; the entry below only covers the round of work it describes.

| Date | Tool | Task | What was checked by running it |
|---|---|---|---|
| 2026-11-24 | Claude (Anthropic), via Claude Code | Audit of the repository against the course requirements, then fixes to the protocol (SACK scoreboard, once-per-window Reno response, timers, FIN/SYN retransmission, idle and stall time-outs, window size, send speed, logging), the lossy proxy, the Mininet topology, the experiment runner, the capture tool, the Wireshark dissector, the setup scripts and the documentation | Unit and localhost impairment tests (67); a 10 MiB x 10-run soak at 0/2/5% loss through the proxy; the topology, runner and capture tool executed in real Mininet + Open vSwitch (without netem, which the kernel lacked); the dissector run in tshark 4.2.2. Not run: netem delay/loss, the full experiment matrix, a fresh-VM reproduction (see docs/STATUS.md) |
| 2026-11-24 | Claude (Anthropic) | Review of the code against the proposal; fixed two congestion-control bugs: (1) staggered RTO expiries in one episode were each reported as a new timeout, collapsing ssthresh to 2 (`rudp/sender.py`, `_rto_point`); (2) a new SACK loss event during SACK recovery was ignored, freezing cwnd (`RenoController.on_fast_retransmit`). Added two regression tests in `tests/test_sender.py`. | 69 tests pass; both new tests fail on the old code. A userspace 10 Mbit/s, 100-packet-queue, 100 ms RTT bottleneck on localhost: 4 MiB goodput 3.5 -> 7.2 Mbit/s, timeout events 65 -> 1. Not run in Mininet. |
| 2026-11-24 | Claude (Anthropic) | Version 2.2: buffered event logging (logged runs no longer slower); Exp4 fairness measured over the window in which both flows run (`experiments/fairness.py`); queueing-delay column; `--progress` live cwnd output; `tools/live_demo.py`; a fifth capture (flow control, 16-packet window); `docs/SCREENSHOT_CHECKLIST.md`; REPORT.md sections 3 to 7 and 10 expanded (design text only, no results). | 72 tests pass (3 new for fairness). Localhost: logged 158 vs unlogged 176 Mbit/s. Runner Exp4 path and the live demo run end to end against a mocked network and fake iperf3; not run in Mininet. |
| 2026-11-24 | Claude (Anthropic) | Version 2.3, closing gaps against the proposal: `CubicController` (RFC 8312/9438: beta 0.7, fast convergence, cubic growth, TCP-friendly region) on top of the Reno controller, with `send_file.py --cc cubic`; Reno refactored into two overridable hooks (no behaviour change); runner pings h1 -> h2 before each run (independent measured RTT for Exp2), Exp4 split into `fairness_reno` and `fairness_cubic`, `--rudp-cc reno cubic`; plots and report tables updated; `tests/test_cubic.py` and runner tests. | 87 tests pass (15 new). Localhost through the lossy proxy, 4 MiB, 50 ms RTT, 1% loss, one run each: Reno 2.89 and CUBIC 2.43 Mbit/s, both SHA-256 correct; CUBIC log shows the 0.7 reduction and fast convergence. Not run in Mininet. |

## Notes for the team

- Findings made while testing, so they are not mistaken for earlier design decisions:
  Mininet rejects switch names that are not `s<number>` without a DPID; the original
  dissector could not load because Wireshark already defines a "Reliable UDP"
  protocol; captures on the bottleneck interface do not show packets the queue drops.
- Results, discussion, the architecture figure, names and the contributions section
  are not AI-written and must come from the team's own measurements and work.
