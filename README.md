# RUDP

Reliable Transport over UDP for CS3001. The project implements a
selective-repeat transport with CRC32 validation, SACK acknowledgements,
receiver flow control, Reno congestion control, RTT/RTO estimation, a routed
Mininet topology, and reproducible comparison experiments against TCP.

## Ubuntu 22.04/24.04 setup

Install system dependencies and create the project virtual environment:

```bash
git clone https://github.com/GhufranMianoor/RUDP.git
cd RUDP
sudo scripts/setup_ubuntu.sh
source .venv/bin/activate
```

The setup script installs Mininet, Open vSwitch, iperf3, Wireshark/tshark,
Python venv support, and the Python dependencies in `requirements.txt`.

## Local transfer

```bash
source .venv/bin/activate
python apps/recv_file.py 9000 received.bin
python apps/send_file.py 127.0.0.1 9000 input.bin --cc reno --log results/local.jsonl
```

## Tests

```bash
python -m pytest -q
```

## Routed topology

All topology operations require root:

```bash
sudo .venv/bin/python topology/topo.py \
  --delay 50ms --loss 0% --rate 10mbit --check
```

The topology contains h1/h3 on `10.0.1.0/24`, h2/h4 on `10.0.3.0/24`,
and Linux routers R1/R2 joined by `10.0.2.0/24`. The `--check` mode prints
routes, verifies a traceroute through both routers, and performs a SHA-256
file-transfer check.

## Experiments and plots

Run the complete workflow as root:

```bash
sudo scripts/run_all.sh
```

The experiment runner performs three 10 MiB runs for each loss, RTT, and
bottleneck-rate condition, plus the fairness condition. It writes:

- `results/raw.csv` and `results/summary.csv`
- JSON-lines logs under `results/logs/`
- pcapng output under `results/pcaps/`
- PNG plots under `results/plots/`

To validate the matrix without network tools:

```bash
python experiments/run_experiments.py --dry-run --runs 1 --results /tmp/rudp-results
```

## Packet captures

Capture the four requested scenarios on a router interface:

```bash
sudo tools/capture_runs.sh R1-eth3 results/pcaps 30
```

Load `tools/rudp_dissector.lua` in Wireshark via **Analyze > Lua Plugins**.
The dissector recognizes RUDP on UDP port 9000.

## Known limitations

Mininet, `tc`, `dumpcap`, and `iperf3` require an Ubuntu host with root
privileges and cannot be fully exercised in a normal CI container. The
experiment harness provides `--dry-run` for validating its output schema
without those system services.
