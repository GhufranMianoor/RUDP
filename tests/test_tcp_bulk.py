import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "apps" / "tcp_bulk.py"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TcpBulkTests(unittest.TestCase):
    def test_every_byte_is_counted_and_intervals_add_up(self):
        port, nbytes = free_port(), 3 * 1024 * 1024 + 123
        server = subprocess.Popen([sys.executable, str(TOOL), "server", str(port),
                                   "--interval", "0.001"], stdout=subprocess.PIPE, text=True)
        client = subprocess.run([sys.executable, str(TOOL), "client", "127.0.0.1", str(port),
                                 str(nbytes), "--cc", "reno"],
                                capture_output=True, text=True, timeout=30)
        server_out, _ = server.communicate(timeout=30)
        self.assertEqual(client.returncode, 0, client.stdout + client.stderr)
        result = json.loads(client.stdout)
        self.assertEqual(result["end"]["sum_received"]["bytes"], nbytes)
        self.assertEqual(result["end"]["sum_sent"]["bytes"], nbytes)
        self.assertEqual(result["tcp"]["cc"], "reno")
        intervals = json.loads(server_out)["intervals"]
        self.assertEqual(sum(i["sum"]["bytes"] for i in intervals), nbytes)
        ends = [i["sum"]["end"] for i in intervals]
        self.assertEqual(ends, sorted(ends))


if __name__ == "__main__":
    unittest.main()
