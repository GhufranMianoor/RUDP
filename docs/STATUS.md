# Final Week Audit Status

Audit performed on `final_week` after the baseline test run (`26 passed`).

| Requirement | Status | Existing evidence / gaps |
|---|---|---|
| A. Protocol core | DONE | [`rudp/packet.py`](/workspaces/RUDP/rudp/packet.py), [`rudp/sender.py`](/workspaces/RUDP/rudp/sender.py), [`rudp/receiver.py`](/workspaces/RUDP/rudp/receiver.py), and [`rudp/connection.py`](/workspaces/RUDP/rudp/connection.py) now implement the specified 32-byte header, CRC/SACK, connection IDs, retransmitted handshake, FIN linger, adaptive RTO, effective flow-control windows, zero-window probing, and selective-repeat transfer. [`tools/lossy_proxy.py`](/workspaces/RUDP/tools/lossy_proxy.py) provides loss, duplication, delay, and corruption injection. Empty, baseline, and 10 MB SHA-256 transfer tests pass. |
| B. Mininet topology | DONE | [`topology/topo.py`](/workspaces/RUDP/topology/topo.py) defines h1/h3 and h2/h4 subnets, R1/R2 forwarding and routes, bidirectional `tc` impairments, root validation, and `--check` diagnostics plus SHA-256 transfer validation. |
| C. Experiments and measurements | DONE | [`experiments/run_experiments.py`](/workspaces/RUDP/experiments/run_experiments.py) defines the requested loss, RTT, rate, and fairness matrix, defaults to three 10 MiB runs, invokes RUDP and iperf3 Reno/CUBIC, computes Jain fairness, and writes `results/raw.csv`, `results/summary.csv`, `results/logs/`, and `results/pcaps/`. |
| D. Packet captures and dissector | DONE | [`tools/capture_runs.sh`](/workspaces/RUDP/tools/capture_runs.sh) captures handshake, loss-recovery, window-growth, and teardown runs as pcapng on a router interface; [`tools/rudp_dissector.lua`](/workspaces/RUDP/tools/rudp_dissector.lua) dissects the 32-byte header and SACK blocks on UDP port 9000. |
| E. Analysis and plots | DONE | [`analysis/plot_results.py`](/workspaces/RUDP/analysis/plot_results.py) generates PNGs for goodput versus loss/RTT/rate, cwnd over time, RTT estimate versus measured RTT, and Jain fairness from CSVs and JSON-lines logs. |
| F. Reproducibility | PARTIAL | [`requirements.txt`](/workspaces/RUDP/requirements.txt) contains only pytest and [`README.md`](/workspaces/RUDP/README.md) has basic local transfer instructions. Ubuntu setup, one-command execution, exact topology/experiment instructions, and [`docs/AI_USE_LOG.md`](/workspaces/RUDP/docs/AI_USE_LOG.md) are missing. |

## Existing Supporting Work

- RTT sampling and Karn's algorithm: [`rudp/rtt.py`](/workspaces/RUDP/rudp/rtt.py)
- Reno and fixed-window controllers: [`rudp/controller.py`](/workspaces/RUDP/rudp/controller.py)
- JSON-lines event logging: [`rudp/eventlog.py`](/workspaces/RUDP/rudp/eventlog.py)
- Design notes: [`docs/congestion_controller_design.md`](/workspaces/RUDP/docs/congestion_controller_design.md)
- Existing tests: [`tests/`](/workspaces/RUDP/tests/)
