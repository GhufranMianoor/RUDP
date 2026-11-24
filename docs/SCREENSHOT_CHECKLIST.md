# Wireshark screenshot checklist

The teacher asks for annotated screenshots that identify the relevant packets,
addresses, ports, protocol fields, flags and message exchanges. Take one
screenshot (or two) per trace below, then annotate it (arrows and labels in any
image editor, or Wireshark's packet comments plus a caption).

## Before you start

1. Produce the traces on the lab VM: `sudo .venv/bin/python tools/capture_evidence.py`.
2. Open Wireshark **as your normal user**, not root (Wireshark disables Lua for root):

   ```bash
   wireshark -X lua_script:tools/rudp_dissector.lua results/pcaps/handshake.pcapng
   ```

   Or load the script once with **Analyze > Lua Plugins** / copy it to
   `~/.local/lib/wireshark/plugins/`.
3. Check the Info column shows RUDP text such as `ACK ack=235 win=1023 SACK[236-236]`.
   If it shows plain UDP, the dissector is not loaded.
4. Use a layout where all three panes are visible: packet list, packet details
   (with the RUDP tree expanded), and bytes.
5. Save each screenshot in `results/evidence/` as `NN_<trace>.png`, and keep the
   original `.pcapng` files next to them; do not edit or re-create captures.

## Every screenshot must label

- [ ] Frame numbers of the packets discussed
- [ ] Source and destination IP (10.0.1.10 h1, 10.0.3.10 h2)
- [ ] UDP ports (h1's ephemeral port and 9000)
- [ ] RUDP flags (SYN, ACK, FIN, DATA)
- [ ] Connection ID (same in both directions)
- [ ] Sequence and acknowledgement numbers
- [ ] Window field
- [ ] CRC32 field (the dissector shows it but does not verify it; the receiver does)
- [ ] A one-sentence caption saying what the exchange proves

## Per trace

| # | Trace | Display filter | What to show and annotate |
|---|---|---|---|
| 1 | `handshake.pcapng` | `rudpcn.flags.syn == 1 \|\| frame.number <= 12` | SYN (h1 -> h2), SYN+ACK (h2 -> h1), ACK, then the first DATA with sequence 1. Mark the connection ID matching in all of them and the timestamp echo in the SYN+ACK. |
| 2 | `loss-recovery.pcapng` | `rudpcn.sack_count > 0 \|\| rudpcn.flags.data == 1` | An ACK whose cumulative ack stops at N while SACK ranges cover packets above N (the hole), the retransmission of N (same sequence number, later timestamp), and the next ACK jumping past the hole. Annotate the SACK ranges as inclusive. |
| 3 | `window-growth.pcapng` | `rudpcn` | Use **Statistics > I/O Graph** (or **Statistics > Flow Graph**) with 100 ms intervals and filter `rudpcn.flags.data == 1`: DATA packets per RTT doubling in slow start, then growing slowly. Annotate the slow start and congestion avoidance regions. |
| 4 | `teardown.pcapng` | `rudpcn.flags.fin == 1` | FIN from h1 whose sequence equals the next unused sequence number, FIN+ACK from h2 acknowledging it. Mark that no DATA follows. |
| 5 | `flow-control.pcapng` | `rudpcn` | A run of DATA packets that stops at 16 outstanding; ACKs whose `win` field is below 16 while a hole is buffered, and the sender resuming as ACKs return. |
| 6 | (from the demo) `results/demo/demo.pcapng` if recorded with `--capture` | `rudpcn` | Optional: retransmissions appearing after the loss phase starts. |

## Also capture (not Wireshark)

- [ ] Terminal: `ip route` on h1, R1, R2, h2 and `traceroute` h1 -> h2 showing
      10.0.1.1 -> 10.0.2.2 -> 10.0.3.10 (first part of `tools/live_demo.py`).
- [ ] Terminal: `tc qdisc show` on the bottleneck showing netem and tbf.
- [ ] The cwnd plot from the demo (`results/demo/cwnd_demo.png`).
