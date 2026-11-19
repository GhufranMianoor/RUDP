#!/usr/bin/env bash
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
mkdir -p results/logs results/pcaps

echo "Building topology and running connectivity/SHA check..."
"$python_bin" topology/topo.py --delay 25ms --loss 0% --rate 10mbit --check

echo "Running experiment matrix..."
"$python_bin" experiments/run_experiments.py --results results

echo "Generating plots..."
"$python_bin" analysis/plot_results.py --results results --output results/plots

echo "Completed. Results are in results/."
