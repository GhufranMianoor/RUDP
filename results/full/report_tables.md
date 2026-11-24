### Experiment 1 - packet loss (RTT 100 ms, 10 Mbit/s; loss is per direction)

All values are mean +/- standard deviation over the successful runs.

| Condition | Protocol | OK/runs | Goodput (Mbit/s) | Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |
|---|---|---|---|---|---|---|---|
| loss_0 | RUDP | 3/3 | 6.50 +/- 0.02 | 2.58 +/- 0.01 | 224.0 +/- 8.5 | 117.2 +/- 2.6 | 16.0 +/- 2.6 |
| loss_0 | TCP-CUBIC | 3/3 | 7.40 +/- 0.95 | 2.29 +/- 0.27 | 66.7 +/- 115.5 | 178.2 +/- 32.6 | - |
| loss_0 | TCP-RENO | 3/3 | 8.50 +/- 0.02 | 1.97 +/- 0.00 | 193.7 +/- 13.4 | 115.1 +/- 3.3 | - |
| loss_2 | RUDP | 3/3 | 0.94 +/- 0.27 | 18.69 +/- 4.96 | 33.0 +/- 4.4 | 103.9 +/- 2.6 | 3.2 +/- 2.4 |
| loss_2 | TCP-CUBIC | 3/3 | 0.94 +/- 0.07 | 17.93 +/- 1.31 | 32.0 +/- 1.0 | 107.8 +/- 4.1 | - |
| loss_2 | TCP-RENO | 3/3 | 1.01 +/- 0.10 | 16.75 +/- 1.70 | 28.3 +/- 4.9 | 107.1 +/- 6.9 | - |
| loss_5 | RUDP | 3/3 | 0.40 +/- 0.08 | 42.94 +/- 8.38 | 99.3 +/- 16.1 | 104.9 +/- 2.1 | 4.0 +/- 2.1 |
| loss_5 | TCP-CUBIC | 3/3 | 0.50 +/- 0.04 | 33.65 +/- 3.08 | 70.3 +/- 6.8 | 113.3 +/- 4.6 | - |
| loss_5 | TCP-RENO | 3/3 | 0.54 +/- 0.12 | 32.37 +/- 6.80 | 70.3 +/- 8.1 | 117.8 +/- 7.3 | - |

### Experiment 2 - round-trip time (no loss, 10 Mbit/s)

All values are mean +/- standard deviation over the successful runs.

| Condition | Protocol | OK/runs | Goodput (Mbit/s) | Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |
|---|---|---|---|---|---|---|---|
| rtt_100ms | RUDP | 3/3 | 5.69 +/- 1.11 | 3.03 +/- 0.66 | 203.0 +/- 66.8 | 109.8 +/- 5.9 | 8.8 +/- 5.8 |
| rtt_100ms | TCP-CUBIC | 3/3 | 7.86 +/- 0.46 | 2.14 +/- 0.13 | 147.0 +/- 127.0 | 158.8 +/- 44.3 | - |
| rtt_100ms | TCP-RENO | 3/3 | 8.16 +/- 0.34 | 2.06 +/- 0.09 | 184.3 +/- 2.5 | 131.3 +/- 31.2 | - |
| rtt_200ms | RUDP | 3/3 | 4.57 +/- 0.08 | 3.68 +/- 0.07 | 236.0 +/- 108.3 | 235.3 +/- 25.8 | 33.6 +/- 25.4 |
| rtt_200ms | TCP-CUBIC | 3/3 | 6.85 +/- 0.26 | 2.45 +/- 0.09 | 283.7 +/- 33.3 | 282.7 +/- 45.7 | - |
| rtt_200ms | TCP-RENO | 3/3 | 7.03 +/- 0.03 | 2.39 +/- 0.01 | 249.3 +/- 5.7 | 310.3 +/- 3.4 | - |
| rtt_20ms | RUDP | 3/3 | 7.04 +/- 1.28 | 2.43 +/- 0.40 | 98.3 +/- 80.8 | 75.8 +/- 13.6 | 54.9 +/- 13.6 |
| rtt_20ms | TCP-CUBIC | 3/3 | 9.23 +/- 0.57 | 1.82 +/- 0.12 | 3.7 +/- 3.2 | 125.5 +/- 16.0 | - |
| rtt_20ms | TCP-RENO | 3/3 | 8.24 +/- 1.89 | 2.12 +/- 0.55 | 86.0 +/- 66.9 | 87.0 +/- 2.1 | - |

RTT estimate versus measured RTT (ms; ping = 20 ICMP probes on the idle path before each run):

| Condition | RUDP smoothed RTT | RUDP minimum RTT | ping average +/- sd | ping minimum |
|---|---|---|---|---|
| rtt_100ms (RUDP) | 109.8 | 101.0 | 124.3 +/- 27.8 | 100.5 |
| rtt_200ms (RUDP) | 235.3 | 201.7 | 206.6 +/- 6.0 | 200.5 |
| rtt_20ms (RUDP) | 75.8 | 20.9 | 25.4 +/- 4.6 | 20.4 |

### Experiment 3 - bottleneck rate (RTT 100 ms, no loss)

All values are mean +/- standard deviation over the successful runs.

| Condition | Protocol | OK/runs | Goodput (Mbit/s) | Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |
|---|---|---|---|---|---|---|---|
| rate_10mbps | RUDP | 3/3 | 5.28 +/- 2.08 | 3.67 +/- 1.87 | 304.7 +/- 142.3 | 112.8 +/- 8.2 | 11.5 +/- 8.0 |
| rate_10mbps | TCP-CUBIC | 3/3 | 7.30 +/- 1.48 | 2.37 +/- 0.53 | 146.3 +/- 127.0 | 184.9 +/- 73.7 | - |
| rate_10mbps | TCP-RENO | 3/3 | 6.66 +/- 1.68 | 2.63 +/- 0.64 | 195.7 +/- 10.0 | 227.4 +/- 183.0 | - |
| rate_20mbps | RUDP | 3/3 | 8.88 +/- 0.41 | 1.89 +/- 0.09 | 182.3 +/- 12.9 | 127.2 +/- 25.1 | 25.7 +/- 25.2 |
| rate_20mbps | TCP-CUBIC | 3/3 | 13.85 +/- 0.28 | 1.21 +/- 0.02 | 294.0 +/- 33.5 | 134.0 +/- 21.0 | - |
| rate_20mbps | TCP-RENO | 3/3 | 12.65 +/- 0.83 | 1.33 +/- 0.09 | 174.0 +/- 124.3 | 104.0 +/- 4.3 | - |
| rate_5mbps | RUDP | 3/3 | 3.86 +/- 0.10 | 4.35 +/- 0.11 | 176.0 +/- 16.6 | 191.9 +/- 1.3 | 90.2 +/- 1.4 |
| rate_5mbps | TCP-CUBIC | 3/3 | 4.62 +/- 0.04 | 3.63 +/- 0.03 | 45.0 +/- 6.2 | 253.7 +/- 2.2 | - |
| rate_5mbps | TCP-RENO | 3/3 | 4.57 +/- 0.03 | 3.67 +/- 0.03 | 150.7 +/- 9.3 | 186.7 +/- 3.4 | - |

### Experiment 4 - RUDP competing with TCP Reno and with TCP CUBIC on a 10 Mbit/s bottleneck (RTT 20, 100, 200 ms)

All values are mean +/- standard deviation over the successful runs.

| Condition | Protocol | OK/runs | Goodput (Mbit/s) | Completion (s) | Retransmissions | RUDP smoothed RTT (ms) | RUDP queueing delay (ms) |
|---|---|---|---|---|---|---|---|
| fairness_cubic_rtt100ms | RUDP | 3/3 | 2.39 +/- 0.00 | 7.03 +/- 0.01 | 18.0 +/- 1.0 | 107.6 +/- 8.4 | 6.7 +/- 8.5 |
| fairness_cubic_rtt100ms | TCP-CUBIC | 3/3 | 7.93 +/- 0.17 | 2.12 +/- 0.05 | 239.3 +/- 22.5 | 173.3 +/- 54.0 | - |
| fairness_cubic_rtt200ms | RUDP | 3/3 | 1.33 +/- 0.04 | 12.63 +/- 0.34 | 15.7 +/- 0.6 | 212.9 +/- 12.4 | 12.3 +/- 12.5 |
| fairness_cubic_rtt200ms | TCP-CUBIC | 3/3 | 6.48 +/- 0.10 | 2.59 +/- 0.04 | 314.7 +/- 55.3 | 246.4 +/- 46.5 | - |
| fairness_cubic_rtt20ms | RUDP | 3/3 | 4.71 +/- 0.54 | 3.59 +/- 0.40 | 40.7 +/- 41.0 | 72.8 +/- 27.6 | 45.2 +/- 16.6 |
| fairness_cubic_rtt20ms | TCP-CUBIC | 3/3 | 6.87 +/- 1.91 | 2.60 +/- 0.86 | 9.0 +/- 3.6 | 98.5 +/- 48.2 | - |
| fairness_reno_rtt100ms | RUDP | 3/3 | 2.59 +/- 0.24 | 6.51 +/- 0.64 | 20.3 +/- 5.9 | 103.1 +/- 0.9 | 2.5 +/- 0.9 |
| fairness_reno_rtt100ms | TCP-RENO | 3/3 | 7.13 +/- 0.98 | 2.38 +/- 0.36 | 198.0 +/- 40.0 | 144.5 +/- 23.9 | - |
| fairness_reno_rtt200ms | RUDP | 3/3 | 1.28 +/- 0.10 | 13.18 +/- 1.05 | 14.3 +/- 1.2 | 204.2 +/- 2.5 | 3.5 +/- 2.5 |
| fairness_reno_rtt200ms | TCP-RENO | 3/3 | 5.85 +/- 1.63 | 3.06 +/- 1.02 | 212.7 +/- 100.5 | 277.4 +/- 59.2 | - |
| fairness_reno_rtt20ms | RUDP | 3/3 | 4.78 +/- 0.19 | 3.52 +/- 0.13 | 27.3 +/- 37.9 | 72.4 +/- 14.6 | 32.6 +/- 18.6 |
| fairness_reno_rtt20ms | TCP-RENO | 3/3 | 7.15 +/- 1.74 | 2.45 +/- 0.67 | 120.7 +/- 50.5 | 98.4 +/- 32.5 | - |

Fairness while both flows run (RUDP row of each condition; mean +/- sd over runs, range in brackets):

| Condition | Competing TCP | RTT | OK/runs | RUDP share of throughput (%) | Jain's fairness index |
|---|---|---|---|---|---|
| fairness_cubic_rtt100ms | CUBIC | 100ms | 3/3 | 5.8 +/- 0.4 | 0.562 +/- 0.004 [0.557-0.565] |
| fairness_cubic_rtt200ms | CUBIC | 200ms | 3/3 | 4.4 +/- 0.2 | 0.546 +/- 0.002 [0.544-0.548] |
| fairness_cubic_rtt20ms | CUBIC | 20ms | 3/3 | 29.1 +/- 23.7 | 0.779 +/- 0.178 [0.664-0.984] |
| fairness_reno_rtt100ms | Reno | 100ms | 3/3 | 8.6 +/- 1.8 | 0.593 +/- 0.021 [0.570-0.611] |
| fairness_reno_rtt200ms | Reno | 200ms | 3/3 | 4.2 +/- 0.2 | 0.544 +/- 0.002 [0.542-0.545] |
| fairness_reno_rtt20ms | Reno | 20ms | 3/3 | 25.7 +/- 21.0 | 0.773 +/- 0.205 [0.600-1.000] |

