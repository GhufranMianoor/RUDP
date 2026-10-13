"""Packet encoding tests."""

import unittest

from rudp.packet import ChecksumError, Packet, PacketFormatError, SACKBlock


class PacketTests(unittest.TestCase):
    def test_round_trip_with_sacks(self) -> None:
        packet = Packet(
            flags=3,
            sequence=12,
            acknowledgement=9,
            window=20,
            payload=b"payload",
            sack_blocks=(SACKBlock(14, 16), SACKBlock(20, 20)),
        )
        self.assertEqual(Packet.decode(packet.encode()), packet)

    def test_bad_crc_is_rejected(self) -> None:
        raw = bytearray(Packet(payload=b"payload").encode())
        raw[-1] ^= 1
        with self.assertRaises(ChecksumError):
            Packet.decode(raw)

    def test_bad_length_is_rejected(self) -> None:
        with self.assertRaises(PacketFormatError):
            Packet.decode(b"short")


if __name__ == "__main__":
    unittest.main()
