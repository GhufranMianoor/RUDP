"""Protocol constants."""

FLAG_SYN = 0x01
FLAG_ACK = 0x02
FLAG_FIN = 0x04
FLAG_DATA = 0x08
FLAG_RST = 0x10

MAX_PAYLOAD = 1200
# Receive window / maximum sequence span, in packets. 1024 x 1200 B = 1.2 MB,
# which exceeds the largest bandwidth-delay product tested (20 Mbps x 200 ms
# = 500 KB), so the congestion window, not this cap, limits throughput.
DEFAULT_WINDOW = 1024
# RFC 6298: the initial RTO is 1 s. It is replaced by measured values after the
# first RTT sample (the handshake does not produce one).
DEFAULT_RTO = 1.0
MIN_RTO = 0.2
MAX_RTO = 5.0
HEADER_SIZE = 32
# Number of SACKed packets above a hole needed to declare it lost.
DUP_ACK_THRESHOLD = 3
