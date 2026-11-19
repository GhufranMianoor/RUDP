#!/usr/bin/env python3
"""Build and validate the four-host, two-router RUDP evaluation topology."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from mininet.cli import CLI
    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import Host, OVSController
    from mininet.topo import Topo
except ImportError:  # pragma: no cover - depends on the Ubuntu/Mininet host
    CLI = TCLink = Mininet = Host = OVSController = Topo = None  # type: ignore[assignment]


LEFT = ("10.0.1.0/24", "10.0.1.1")
MIDDLE = ("10.0.2.0/24",)
RIGHT = ("10.0.3.0/24", "10.0.3.1")


if Topo is not None:

    class RoutedTopo(Topo):
        """h1/h3 -- R1 -- R2 -- h2/h4 with Linux router nodes."""

        def build(self) -> None:
            r1 = self.addHost("R1", cls=Host, ip=None)
            r2 = self.addHost("R2", cls=Host, ip=None)
            left = [self.addHost(name, ip=f"10.0.1.{10 + i}/24")
                    for i, name in enumerate(("h1", "h3"))]
            right = [self.addHost(name, ip=f"10.0.3.{10 + i}/24")
                     for i, name in enumerate(("h2", "h4"))]
            for host in left:
                self.addLink(host, r1)
            for host in right:
                self.addLink(r2, host)
            self.addLink(r1, r2)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("topology must be run as root (use sudo)")


def _configure_routes(net: Mininet) -> None:
    r1, r2 = net["R1"], net["R2"]
    r1.cmd("sysctl -w net.ipv4.ip_forward=1")
    r2.cmd("sysctl -w net.ipv4.ip_forward=1")
    r1_intfs = r1.intfList()
    r2_intfs = r2.intfList()
    r1.cmd(f"ip addr add 10.0.1.1/24 dev {r1_intfs[0].name} 2>/dev/null || true")
    r1.cmd(f"ip addr add 10.0.2.1/24 dev {r1_intfs[-1].name} 2>/dev/null || true")
    r2.cmd(f"ip addr add 10.0.2.2/24 dev {r2_intfs[-1].name} 2>/dev/null || true")
    r2.cmd(f"ip addr add 10.0.3.1/24 dev {r2_intfs[-1].name} 2>/dev/null || true")
    for host in ("h1", "h3"):
        net[host].cmd("ip route replace default via 10.0.1.1")
    for host in ("h2", "h4"):
        net[host].cmd("ip route replace default via 10.0.3.1")
    r1.cmd("ip route replace 10.0.3.0/24 via 10.0.2.2")
    r2.cmd("ip route replace 10.0.1.0/24 via 10.0.2.1")


def _apply_impairment(net: Mininet, delay: str, loss: str, rate: str) -> None:
    r1, r2 = net["R1"], net["R2"]
    for router in (r1, r2):
        middle = [intf for intf in router.intfList() if "R" in intf.name]
        if not middle:
            raise RuntimeError(f"could not identify router-to-router interface on {router.name}")
        interface = middle[0].name
        commands = (
            f"tc qdisc replace dev {interface} root handle 1: netem "
            f"delay {delay} loss {loss}",
            f"tc qdisc replace dev {interface} parent 1:1 handle 10: "
            f"tbf rate {rate} burst 32kbit latency 400ms limit 1000",
        )
        for command in commands:
            result = router.cmd(command)
            if result:
                raise RuntimeError(f"{router.name}: {command}\n{result}")


def _run_check(net: Mininet, project_root: Path) -> None:
    for name in ("h1", "h2", "h3", "h4", "R1", "R2"):
        print(f"\n== {name} routes ==")
        print(net[name].cmd("ip route"))
    print("\n== traceroute h1 -> h2 ==")
    print(net["h1"].cmd("traceroute -n -m 5 -w 1 10.0.3.10"))
    print("\n== SHA-256 transfer check ==")
    source = "/tmp/rudp-topology-check.bin"
    payload = project_root / "README.md"
    net["h1"].cmd(f"cp {payload} {source}")
    net["h2"].cmd(f"rm -f {source}.received")
    receiver = net["h2"].popen(
        [sys.executable, str(project_root / "apps/recv_file.py"), "9000", f"{source}.received"]
    )
    net["h1"].cmd(
        f"{sys.executable} {project_root / 'apps/send_file.py'} 10.0.3.10 9000 {source}"
    )
    receiver.wait(timeout=30)
    original = subprocess.check_output(["sha256sum", str(payload)], text=True).split()[0]
    received = net["h2"].cmd(f"sha256sum {source}.received").split()[0]
    if original != received:
        raise RuntimeError(f"SHA-256 mismatch: expected {original}, received {received}")
    print(f"SHA-256 match: {original}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", default="50ms")
    parser.add_argument("--loss", default="0%")
    parser.add_argument("--rate", default="10mbit")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    _require_root()
    if Mininet is None:
        raise SystemExit("Mininet is required; install it with scripts/setup_ubuntu.sh")
    project_root = Path(__file__).resolve().parents[1]
    net = Mininet(topo=RoutedTopo(), link=TCLink, controller=OVSController)
    try:
        net.start()
        _configure_routes(net)
        _apply_impairment(net, args.delay, args.loss, args.rate)
        if args.check:
            _run_check(net, project_root)
        else:
            CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
