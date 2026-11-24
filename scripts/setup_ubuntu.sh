#!/usr/bin/env bash
# Install everything needed on Ubuntu 22.04 / 24.04 (run once, as root).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo scripts/setup_ubuntu.sh" >&2
  exit 1
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ethtool \
  iperf3 \
  iproute2 \
  iputils-ping \
  mininet \
  openvswitch-switch \
  python3-venv \
  tcpdump \
  traceroute \
  tshark \
  wireshark

# Mininet is installed by apt into the system Python, so the virtual
# environment must be able to import it (otherwise `sudo .venv/bin/python
# topology/topo.py` fails with "No module named mininet").
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# Open vSwitch must be running for the OVSBridge switches. On hosts without the
# kernel module (some VMs/containers) topology/topo.py falls back to the
# userspace datapath automatically.
systemctl enable --now openvswitch-switch 2>/dev/null || service openvswitch-switch start || true

# Fail now, not in the middle of an experiment.
.venv/bin/python - <<'PY'
import mininet.net  # noqa: F401
import matplotlib   # noqa: F401
print("python imports OK")
PY
for tool in iperf3 tc ip ping traceroute tcpdump dumpcap tshark ethtool ovs-vsctl; do
  command -v "$tool" >/dev/null || { echo "MISSING: $tool" >&2; exit 1; }
done

echo "Setup complete. Next: .venv/bin/python -m pytest -q && sudo .venv/bin/python topology/topo.py --check"
