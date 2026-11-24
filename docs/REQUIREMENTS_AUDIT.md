# Teacher Requirements — Implementation and Evidence Tracker

This file separates source-code presence from evidence that must be generated
on the Ubuntu/Mininet lab machine. A script or placeholder is not proof of a
successful live demonstration.

| Requirement | Project location | Current package status | Evidence still needed |
|---|---|---|---|
| Architecture with subnets, IPs, ports, nodes, routers/switches and flows | `docs/ARCHITECTURE.md`, `topology/topo.py` | Address plan and topology implementation documented; switches, routing, traceroute, tbf and RUDP verified in real Mininet (without netem) | Run Mininet; save topology screenshot and routing/traceroute output |
| Working routed POC | `apps/`, `rudp/`, `topology/topo.py`, `tools/live_demo.py` | Application, topology and a scripted live demo (routes, traceroute, transfer with loss/delay changed mid-run, live cwnd, SHA-256, cwnd plot) included | Live h1-to-h2 transfer through R1/R2; record SHA-256 match |
| Packet captures for key exchanges | `tools/capture_evidence.py`, `tools/rudp_dissector.lua` | Capture tool run in Mininet (four traces produced and decoded in development); dissector fixed and tested with tshark 4.2.2 | Actual non-empty `.pcapng` for handshake, loss recovery, window growth, teardown and flow control |
| Annotated packet screenshots | `docs/SCREENSHOT_CHECKLIST.md` | Per-trace filters and labels documented | Screenshots showing addresses, UDP ports, RUDP flags, sequence/ACK, window and SACK fields |
| At least 3 network conditions and 3 runs each | `experiments/run_experiments.py` | Experiment matrix code included | Execute real runs; inspect raw CSV; report means and standard deviations |
| TCP Reno/CUBIC comparison and fairness | `experiments/run_experiments.py` | Comparison/fairness workflow included | Verify iperf3 processes actually traverse the Mininet topology and share the same bottleneck |
| 6–10 page report | `REPORT.md` | Draft report included | Replace planned/claimed outcomes with measured results and evidence; verify final page count |
| Reproducibility | `README.md`, `scripts/` | Setup/run instructions included | Fresh Ubuntu reproduction test by another team member |

## Important validation caveat

The experiment runner and Mininet topology must be validated together before
accepting any CSV as final evidence. In particular, confirm that experiment
traffic is launched inside the Mininet hosts and traverses the R1–R2 link.
A localhost run, a dry run, or an empty capture does not satisfy the routed
experiment requirement.

## Completion rule

Mark a row complete only after its evidence file/output exists and has been
inspected. Keep raw run data and original captures; do not invent measurements
or reconstruct packet captures.
