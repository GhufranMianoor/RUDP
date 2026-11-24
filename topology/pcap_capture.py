"""Packet capture inside a Mininet node, with a clear error when it fails.

Used by tools/capture_evidence.py, experiments/run_experiments.py and
tools/live_demo.py. The capture runs inside the node's network namespace (R1),
so it sees exactly what crosses that router interface.

Why not simply ``dumpcap -w results/pcaps/x.pcapng``: if the capture tool
cannot start (or cannot create the file) inside the namespace, it exits at
once and nothing is written; with its error output discarded, the first sign
was a ``FileNotFoundError`` long after the transfer. Here:

* the tool writes to a fresh, world-writable directory under /tmp first (no
  home-directory permissions or AppArmor path rules involved) and the file is
  moved to its final place when the capture stops;
* if dumpcap exits during start-up, its error is printed and tcpdump is tried
  (``-Z root`` so it does not switch to the ``tcpdump`` user before writing;
  its .pcap is converted to .pcapng with ``editcap`` when available);
* ``stop()`` returns the final path, or None with the reason in ``error``,
  instead of raising, so one failed capture cannot abort a whole run.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


class Capture:
    def __init__(self, node, interface: str, destination: Path, port: int = 9000,
                 snaplen: int = 256) -> None:
        self.node = node
        self.interface = interface
        self.destination = Path(destination)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.capture_filter = f"udp port {port}"
        self.snaplen = str(snaplen)
        self.stage = Path(tempfile.mkdtemp(prefix="rudp-capture-"))
        os.chmod(self.stage, 0o777)
        self.process: subprocess.Popen | None = None
        self.tool = ""
        self.file: Path | None = None
        self.error = ""
        self._start()

    # ----------------------------------------------------------------- start
    def _launch(self, tool: str, command: list[str], file: Path) -> bool:
        process = self.node.popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(1.0)  # let the capture attach before traffic starts
        if process.poll() is None:
            self.process, self.tool, self.file = process, tool, file
            return True
        message = process.stderr.read().decode(errors="replace").strip() if process.stderr else ""
        self.error += f"{tool} stopped at start-up (exit code {process.returncode}): {message or 'no message'}\n"
        return False

    def _start(self) -> None:
        if shutil.which("dumpcap"):
            file = self.stage / "capture.pcapng"
            if self._launch("dumpcap", ["dumpcap", "-q", "-i", self.interface, "-f", self.capture_filter,
                                        "-s", self.snaplen, "-w", str(file)], file):
                return
        if shutil.which("tcpdump"):
            file = self.stage / "capture.pcap"
            if self._launch("tcpdump", ["tcpdump", "-i", self.interface, "-s", self.snaplen, "-U",
                                        "-Z", "root", "-w", str(file), self.capture_filter], file):
                return
        if not self.error:
            self.error = "neither dumpcap nor tcpdump is installed (sudo apt install -y tshark tcpdump)\n"
        print(f"  capture could not start on {self.interface}:\n    " +
              self.error.strip().replace("\n", "\n    "), flush=True)

    # ------------------------------------------------------------------ stop
    def stop(self) -> Path | None:
        """Stop the capture; return the saved file, or None (reason in ``error``)."""

        try:
            if self.process is None:
                return None
            time.sleep(0.5)  # let the last packets reach the capture
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            message = self.process.stderr.read().decode(errors="replace").strip() if self.process.stderr else ""
            self.process = None
            if self.file is None or not self.file.exists() or self.file.stat().st_size == 0:
                self.error += f"{self.tool} wrote no capture file: {message or 'no message'}\n"
                print(f"  capture failed: {self.error.strip()}", flush=True)
                return None
            target = self.destination
            source = self.file
            if source.suffix == ".pcap":
                if shutil.which("editcap"):
                    converted = self.stage / "capture.pcapng"
                    subprocess.run(["editcap", "-F", "pcapng", str(source), str(converted)],
                                   check=False, capture_output=True)
                    if converted.exists() and converted.stat().st_size > 0:
                        source = converted
                if source.suffix == ".pcap":
                    target = target.with_suffix(".pcap")
            shutil.move(str(source), str(target))
            target.chmod(0o644)
            owner = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
            if all(owner):
                try:
                    os.chown(target, int(owner[0]), int(owner[1]))
                except OSError:
                    pass
            return target
        finally:
            shutil.rmtree(self.stage, ignore_errors=True)

    def kill(self) -> None:
        """Stop without saving (used on error paths)."""

        if self.process is not None:
            self.process.kill()
            self.process = None
        shutil.rmtree(self.stage, ignore_errors=True)
