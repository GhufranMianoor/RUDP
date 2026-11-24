#!/usr/bin/env python3
"""Two-router, three-subnet Mininet topology for the RUDP experiments.

    h1 ┐                                   ┌ h2   (RUDP: UDP 9000)
       ├ sA ── R1 ══════ R2 ── sC ┤
    h3 ┘   10.0.1.0/24  10.0.2.0/24  10.0.3.0/24   └ h4   (iperf3: TCP 5201)

Subnets A and C are shared Ethernet LANs (Open vSwitch learning bridges);
subnet B is the point-to-point R1-R2 bottleneck. Delay, loss and rate are
applied with ``tc`` on both egress interfaces of the bottleneck:

* netem:  ``delay`` one-way on each egress (RTT = 2 x delay) and ``loss``
  *per direction* (a ping round trip therefore sees about 1-(1-p)^2 ~ 2p).
* tbf:    the rate limit and a drop-tail queue of ``queue_packets`` MTU-sized
  packets (default 100), created as a child of netem.

Usable as a script (``sudo python topology/topo.py --check``) and as a library
(``build_network``), which the experiment runner uses to build one fresh
network per experimental condition.
"""

from __future__ import annotations

import argparse
from functools import partial
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from mininet.cli import CLI
    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import Host, OVSBridge
    from mininet.topo import Topo
except ImportError:  # Mininet is available only on the Ubuntu lab host
    CLI = TCLink = Mininet = Host = OVSBridge = Topo = None  # type: ignore[assignment]

MTU = 1500
SUBNET_A, SUBNET_B, SUBNET_C = "10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"
ADDRESSES = {
    "h1": "10.0.1.10", "h3": "10.0.1.11",
    "h2": "10.0.3.10", "h4": "10.0.3.11",
}
RUDP_PORT = 9000
IPERF_PORT = 5201


# --------------------------------------------------------------------- tc maths

def parse_rate_bps(rate: str) -> int:
    """'10mbit' -> 10_000_000 (tc units: kbit, mbit, gbit, bit)."""

    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([kmg]?)bit", rate.strip().lower())
    if not match:
        raise ValueError(f"unsupported rate {rate!r}; use e.g. 5mbit, 10mbit, 20mbit")
    scale = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9}[match.group(2)]
    return int(float(match.group(1)) * scale)


def tbf_params(rate: str, queue_packets: int) -> tuple[int, int]:
    """Return ``(burst_bytes, limit_bytes)`` for tbf.

    ``burst`` must cover at least rate/HZ (tc rounds timers to the kernel tick)
    and one MTU, otherwise the configured rate is not reachable. ``limit`` is
    the queue size in bytes: ``queue_packets`` MTU-sized packets.
    """

    rate_bytes = parse_rate_bps(rate) // 8
    burst = max(15_000, rate_bytes // 100)
    limit = queue_packets * MTU
    return burst, limit


def impairment_commands(
    interface: str, delay: str, loss: str, rate: str, queue_packets: int = 100
) -> list[str]:
    burst, limit = tbf_params(rate, queue_packets)
    return [
        f"tc qdisc replace dev {interface} root handle 1: netem delay {delay} loss {loss} limit 10000",
        f"tc qdisc replace dev {interface} parent 1:1 handle 10: tbf rate {rate} burst {burst} limit {limit}",
    ]


# ---------------------------------------------------------------------- topology

if Topo is not None:
    class RoutedTopo(Topo):
        """h1/h3 -- sA -- R1 -- R2 -- sC -- h2/h4."""

        def build(self) -> None:
            r1 = self.addHost("R1", cls=Host, ip=None)
            r2 = self.addHost("R2", cls=Host, ip=None)
            # Mininet needs a datapath id unless the name is s<number>.
            s_a = self.addSwitch("sA", dpid="1")
            s_c = self.addSwitch("sC", dpid="2")
            h1 = self.addHost("h1", ip=f"{ADDRESSES['h1']}/24")
            h3 = self.addHost("h3", ip=f"{ADDRESSES['h3']}/24")
            h2 = self.addHost("h2", ip=f"{ADDRESSES['h2']}/24")
            h4 = self.addHost("h4", ip=f"{ADDRESSES['h4']}/24")
            self.addLink(r1, s_a)
            self.addLink(r1, r2)
            self.addLink(r2, s_c)
            for host in (h1, h3):
                self.addLink(host, s_a)
            for host in (h2, h4):
                self.addLink(host, s_c)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("topology must be run as root (use sudo)")


def interface_between(net, node_a: str, node_b: str) -> str:
    """Name of ``node_a``'s interface on its link to ``node_b``.

    Mininet orders links internally, so interface numbers are looked up
    instead of assumed.
    """

    links = net.linksBetween(net[node_a], net[node_b])
    if not links:
        raise RuntimeError(f"no link between {node_a} and {node_b}")
    link = links[0]
    return link.intf1.name if link.intf1.node.name == node_a else link.intf2.name


def _run(node, command: str) -> str:
    result = node.cmd(command)
    if result and result.strip():
        raise RuntimeError(f"{node.name}: {command}: {result.strip()}")
    return result


def router_interfaces(net) -> dict[str, str]:
    return {
        "R1_lan": interface_between(net, "R1", "sA"),
        "R1_wan": interface_between(net, "R1", "R2"),
        "R2_wan": interface_between(net, "R2", "R1"),
        "R2_lan": interface_between(net, "R2", "sC"),
    }


def _configure_routes(net) -> dict[str, str]:
    r1, r2 = net["R1"], net["R2"]
    names = router_interfaces(net)
    for router in (r1, r2):
        router.cmd("sysctl -w net.ipv4.ip_forward=1 >/dev/null")
    plan = (
        (r1, names["R1_lan"], "10.0.1.1/24"),
        (r1, names["R1_wan"], "10.0.2.1/24"),
        (r2, names["R2_wan"], "10.0.2.2/24"),
        (r2, names["R2_lan"], "10.0.3.1/24"),
    )
    for router, interface, address in plan:
        _run(router, f"ip addr flush dev {interface}")
        _run(router, f"ip addr add {address} dev {interface}")
        _run(router, f"ip link set {interface} up")
    for host in ("h1", "h3"):
        _run(net[host], "ip route replace default via 10.0.1.1")
    for host in ("h2", "h4"):
        _run(net[host], "ip route replace default via 10.0.3.1")
    _run(r1, "ip route replace 10.0.3.0/24 via 10.0.2.2")
    _run(r2, "ip route replace 10.0.1.0/24 via 10.0.2.1")
    return names


def _disable_offloads(net, names: dict[str, str], userspace_ovs: bool = False) -> None:
    """GSO/TSO/GRO make tc see 64 KB 'packets'; turn them off for honest queues.

    The OVS userspace datapath (used only where the kernel has no openvswitch
    module) additionally needs checksum offload off, otherwise TCP/UDP
    checksums are never filled in and only ICMP gets through.
    """

    if shutil.which("ethtool") is None:
        if userspace_ovs:
            raise RuntimeError("ethtool is required with the OVS userspace datapath")
        return
    flags = "tso off gso off gro off" + (" tx off rx off" if userspace_ovs else "")
    for node in ("h1", "h2", "h3", "h4"):
        net[node].cmd(f"ethtool -K {node}-eth0 {flags} 2>/dev/null")
    router_for = {"R1_lan": "R1", "R1_wan": "R1", "R2_wan": "R2", "R2_lan": "R2"}
    for key, interface in names.items():
        net[router_for[key]].cmd(f"ethtool -K {interface} {flags} 2>/dev/null")


def apply_impairment(
    net, delay: str, loss: str, rate: str, queue_packets: int = 100, names: dict[str, str] | None = None
) -> None:
    """Configure the bottleneck: egress of R1-R2 in both directions."""

    names = names or router_interfaces(net)
    for router, interface in ((net["R1"], names["R1_wan"]), (net["R2"], names["R2_wan"])):
        for command in impairment_commands(interface, delay, loss, rate, queue_packets):
            _run(router, command)


def build_network(
    delay: str = "10ms",
    loss: str = "0%",
    rate: str = "10mbit",
    queue_packets: int = 100,
    *,
    userspace_ovs: bool | None = None,
):
    """Create, start and configure the network. Caller must ``net.stop()``.

    ``userspace_ovs`` selects the OVS userspace datapath for machines whose
    kernel has no openvswitch module (containers, some VMs); by default it is
    chosen automatically and a normal Ubuntu install uses the kernel module.
    """

    if Mininet is None:
        raise SystemExit("Mininet is required; install it with scripts/setup_ubuntu.sh")
    _require_root()
    if userspace_ovs is None:
        userspace_ovs = not _ovs_kernel_module_available()
    switch = partial(OVSBridge, datapath="user") if userspace_ovs else OVSBridge
    net = Mininet(topo=RoutedTopo(), link=TCLink, switch=switch, controller=None, autoSetMacs=True)
    net.start()
    try:
        names = _configure_routes(net)
        _disable_offloads(net, names, userspace_ovs)
        apply_impairment(net, delay, loss, rate, queue_packets, names)
    except Exception:
        net.stop()
        raise
    net.interface_names = names  # type: ignore[attr-defined]
    return net


def _ovs_kernel_module_available() -> bool:
    """True when the openvswitch kernel module is loaded or loadable."""

    if Path("/sys/module/openvswitch").exists():
        return True
    modprobe = shutil.which("modprobe")
    if modprobe is None:
        return False
    return subprocess.run([modprobe, "-n", "openvswitch"], capture_output=True).returncode == 0


# ------------------------------------------------------------------------- checks

def _hops(output: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", output, re.M)]


def _ping_stats(output: str) -> tuple[float | None, float | None]:
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", output)
    return (float(loss.group(1)) if loss else None, float(rtt.group(1)) if rtt else None)


def _duration_ms(value: str) -> float:
    match = re.fullmatch(r"([0-9.]+)\s*(ms|s|us)?", value.strip())
    if not match:
        raise ValueError(f"unsupported delay {value!r}")
    return float(match.group(1)) * {"ms": 1.0, "s": 1000.0, "us": 0.001, None: 1.0}[match.group(2)]


def run_check(net, project_root: Path, delay: str, loss: str, rate: str) -> bool:
    """Verify addressing, routing, impairment and an end-to-end RUDP transfer."""

    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    for name in ("h1", "h2", "h3", "h4", "R1", "R2"):
        print(f"\n== {name} interfaces and routes ==")
        print(net[name].cmd("ip -br addr"))
        print(net[name].cmd("ip route"))
    print("== impairment on the bottleneck ==")
    for router, key in (("R1", "R1_wan"), ("R2", "R2_wan")):
        print(f"-- {router} {net.interface_names[key]}")
        print(net[router].cmd(f"tc -s qdisc show dev {net.interface_names[key]}"))

    # 1. connectivity
    ping = net["h1"].cmd("ping -c 3 -W 2 10.0.3.10")
    lost, _ = _ping_stats(ping)
    record("ping h1 -> h2", lost is not None and lost < 100, f"{lost}% loss")
    ping = net["h3"].cmd("ping -c 3 -W 2 10.0.3.11")
    lost, _ = _ping_stats(ping)
    record("ping h3 -> h4", lost is not None and lost < 100, f"{lost}% loss")

    # 2. path
    trace = net["h1"].cmd("traceroute -n -m 5 -w 2 10.0.3.10")
    print(trace)
    hops = _hops(trace)
    expected = ["10.0.1.1", "10.0.2.2", "10.0.3.10"]
    record("traceroute h1 -> h2", hops[:3] == expected, f"hops {hops[:3]} expected {expected}")

    # 3. RTT matches the setting (RTT = 2 x one-way delay; transmission adds ~1-2 ms)
    expected_rtt = 2 * _duration_ms(delay)
    ping = net["h1"].cmd("ping -c 20 -i 0.2 -W 2 10.0.3.10")
    _, rtt = _ping_stats(ping)
    ok = rtt is not None and expected_rtt <= rtt <= expected_rtt * 1.2 + 6
    record("ping RTT", ok, f"measured {rtt} ms, configured {expected_rtt:g} ms")

    # 4. loss (informational: random, applied per direction)
    configured = float(loss.rstrip("%")) / 100
    ping = net["h1"].cmd("ping -c 200 -i 0.01 -W 2 10.0.3.10")
    lost, _ = _ping_stats(ping)
    print(
        f"[INFO] ping loss over 200 packets: {lost}% "
        f"(per-direction {configured:.1%} -> expected round-trip about "
        f"{100 * (1 - (1 - configured) ** 2):.1f}%)"
    )

    # 5. rate: iperf3 TCP should reach most of the configured rate
    if shutil.which("iperf3"):
        net["h4"].cmd(f"iperf3 -s -p {IPERF_PORT} -D --one-off")
        time.sleep(1)
        out = net["h3"].cmd(f"iperf3 -c 10.0.3.11 -p {IPERF_PORT} -t 6 -J")
        try:
            bits = json.loads(out)["end"]["sum_received"]["bits_per_second"]
            configured_bps = parse_rate_bps(rate)
            ratio = bits / configured_bps
            lossy = configured > 0
            record(
                "iperf3 TCP throughput",
                ratio >= (0.3 if lossy else 0.8) and ratio <= 1.05,
                f"{bits / 1e6:.2f} Mbit/s = {ratio:.0%} of {rate}"
                + (" (loss lowers TCP throughput)" if lossy else ""),
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            record("iperf3 TCP throughput", False, f"no result: {out.strip()[:200]}")
    else:
        print("[SKIP] iperf3 not installed")

    # 6. RUDP end-to-end with SHA-256
    with tempfile.TemporaryDirectory(prefix="rudp-check-") as work:
        source = Path(work) / "payload.bin"
        source.write_bytes(os.urandom(2 * 1024 * 1024))
        received = Path(work) / "received.bin"
        os.chmod(work, 0o777)
        receiver = net["h2"].popen(
            [sys.executable, str(project_root / "apps/recv_file.py"), str(RUDP_PORT), str(received)]
        )
        try:
            sender = net["h1"].popen(
                [sys.executable, str(project_root / "apps/send_file.py"),
                 "10.0.3.10", str(RUDP_PORT), str(source)],
                stdout=subprocess.PIPE, text=True,
            )
            summary, _ = sender.communicate(timeout=120)
            receiver.wait(timeout=30)
            match = received.exists() and received.read_bytes() == source.read_bytes()
            detail = summary.strip().splitlines()[-1] if summary.strip() else "no output"
            record("RUDP transfer h1 -> h2 (SHA-256)", match and sender.returncode == 0, detail)
        except subprocess.TimeoutExpired:
            record("RUDP transfer h1 -> h2 (SHA-256)", False, "timed out")
        finally:
            for process in (receiver,):
                if process.poll() is None:
                    process.kill()

    failed = [name for name, ok, _ in results if not ok]
    print("\nALL CHECKS PASSED" if not failed else f"\nFAILED: {', '.join(failed)}")
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--delay", default="10ms",
                        help="one-way delay on each bottleneck egress (RTT = 2 x delay)")
    parser.add_argument("--loss", default="0%", help="loss per direction, e.g. 2%%")
    parser.add_argument("--rate", default="10mbit")
    parser.add_argument("--queue-packets", type=int, default=100,
                        help="bottleneck drop-tail queue length in MTU-sized packets")
    parser.add_argument("--check", action="store_true", help="run the verification suite and exit")
    args = parser.parse_args()
    _require_root()
    project_root = Path(__file__).resolve().parents[1]
    net = build_network(args.delay, args.loss, args.rate, args.queue_packets)
    try:
        if args.check:
            return 0 if run_check(net, project_root, args.delay, args.loss, args.rate) else 1
        CLI(net)
        return 0
    finally:
        net.stop()


if __name__ == "__main__":
    raise SystemExit(main())
