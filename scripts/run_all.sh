#!/usr/bin/env bash
# Topology check -> experiment matrix -> evidence captures -> plots.  Run as root.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo scripts/run_all.sh" >&2
  exit 1
fi
if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv; run sudo scripts/setup_ubuntu.sh first" >&2
  exit 1
fi

python_bin="$root/.venv/bin/python"
mkdir -p results/logs results/pcaps results/evidence

mn -c >/dev/null 2>&1 || true   # clear any half-built network from an earlier crash

echo "== 1/4 Topology check (addresses, traceroute, RTT, iperf3 rate, RUDP SHA-256) =="
"$python_bin" topology/topo.py --delay 25ms --loss 0% --rate 10mbit --check

echo "== 2/4 Experiment matrix (3 runs per condition) =="
"$python_bin" experiments/run_experiments.py --results results --runs "${RUNS:-3}"

echo "== 3/4 Evidence captures (handshake, loss recovery, window growth, teardown, flow control) =="
"$python_bin" tools/capture_evidence.py --results results

echo "== 4/4 Plots and report tables =="
"$python_bin" analysis/plot_results.py --results results --output results/plots
"$python_bin" analysis/make_report_tables.py --results results > results/report_tables.md

echo "Completed. Results are in results/ (raw.csv, summary.csv, plots/, pcaps/, evidence/)."
