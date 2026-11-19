#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "capture_runs.sh must be run as root" >&2
  exit 1
fi

iface="${1:-R1-eth3}"
output="${2:-results/pcaps}"
duration="${3:-30}"
mkdir -p "$output"

if ! command -v dumpcap >/dev/null 2>&1; then
  echo "dumpcap is required; install it with scripts/setup_ubuntu.sh" >&2
  exit 1
fi

for scenario in handshake loss-recovery window-growth teardown; do
  file="${output}/${scenario}.pcapng"
  echo "Capture ${scenario} on ${iface} for ${duration}s -> ${file}"
  dumpcap -q -i "$iface" -f "udp port 9000" \
    -a "duration:${duration}" -w "$file"
done
