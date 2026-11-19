"""Wire-format packets for the Reliable UDP protocol.

The fixed header is the 32-byte network-order format
``>BBHIIHHIIIB3x``. SACK blocks follow the fixed header as inclusive
``(start, end)`` sequence-number ranges. The CRC32 covers the header, SACK
blocks, and payload with the checksum field zeroed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
import zlib


VERSION = 1
HEADER_SIZE = 32
MAX_PAYLOAD = 1200
MAX_SACK_BLOCKS = 3

# version, flags, connection id, sequence, acknowledgement, window,
# payload length, timestamp, timestamp echo, checksum, SACK count, reserved
_HEADER = struct.Struct(">BBHIIHHIIIB3x")
_SACK_BLOCK = struct.Struct("!II")
_CHECKSUM_OFFSET = 24


class PacketError(ValueError):
    """Base exception for malformed or invalid packets."""


class PacketFormatError(PacketError):
    """Raised when bytes do not contain a valid packet structure."""


class ChecksumError(PacketError):
    """Raised when a packet fails CRC32 validation."""


@dataclass(frozen=True, slots=True)
class SACKBlock:
    """An inclusive sequence-number range acknowledged by a receiver."""

    start: int
    end: int

    def __post_init__(self) -> None:
        _validate_uint32(self.start, "SACK start")
        _validate_uint32(self.end, "SACK end")
        if self.start > self.end:
            raise PacketError("SACK start must not exceed SACK end")

    def pack(self) -> bytes:
        return _SACK_BLOCK.pack(self.start, self.end)


@dataclass(frozen=True, slots=True)
class Packet:
    """A RUDP packet and its transport metadata."""

    flags: int = 0
    connection_id: int = 0
    sequence: int = 0
    acknowledgement: int = 0
    window: int = 0
    payload: bytes = b""
    timestamp: int = 0
    timestamp_echo: int = 0
    sack_blocks: tuple[SACKBlock, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_uint8(self.flags, "flags")
        _validate_uint16(self.connection_id, "connection id")
        _validate_uint32(self.sequence, "sequence")
        _validate_uint32(self.acknowledgement, "acknowledgement")
        _validate_uint16(self.window, "window")
        _validate_uint32(self.timestamp, "timestamp")
        _validate_uint32(self.timestamp_echo, "timestamp echo")
        if not isinstance(self.payload, bytes):
            raise PacketError("payload must be bytes")
        if len(self.payload) > MAX_PAYLOAD:
            raise PacketError(f"payload exceeds {MAX_PAYLOAD} bytes")

        blocks = tuple(
            block if isinstance(block, SACKBlock) else SACKBlock(*block)
            for block in self.sack_blocks
        )
        if len(blocks) > MAX_SACK_BLOCKS:
            raise PacketError(f"at most {MAX_SACK_BLOCKS} SACK blocks are supported")
        object.__setattr__(self, "sack_blocks", blocks)

    @property
    def seq(self) -> int:
        """Short alias for ``sequence``."""

        return self.sequence

    @property
    def ack(self) -> int:
        """Short alias for ``acknowledgement``."""

        return self.acknowledgement

    def encode(self) -> bytes:
        """Serialize this packet, including its calculated CRC32."""

        header = _HEADER.pack(
            VERSION,
            self.flags,
            self.connection_id,
            self.sequence,
            self.acknowledgement,
            self.window,
            len(self.payload),
            self.timestamp,
            self.timestamp_echo,
            0,
            len(self.sack_blocks),
        )
        sacks = b"".join(block.pack() for block in self.sack_blocks)
        checksum = zlib.crc32(header + sacks + self.payload) & 0xFFFFFFFF
        header = bytearray(header)
        struct.pack_into("!I", header, _CHECKSUM_OFFSET, checksum)
        return bytes(header) + sacks + self.payload

    def pack(self) -> bytes:
        """Alias for :meth:`encode`."""

        return self.encode()

    @classmethod
    def decode(cls, data: bytes | bytearray | memoryview) -> "Packet":
        """Deserialize and validate a complete packet datagram."""

        raw = bytes(data)
        if len(raw) < HEADER_SIZE:
            raise PacketFormatError("packet is shorter than the fixed header")

        (
            version,
            flags,
            connection_id,
            sequence,
            acknowledgement,
            window,
            payload_length,
            timestamp,
            timestamp_echo,
            checksum,
            sack_count,
        ) = _HEADER.unpack_from(raw)

        if version != VERSION:
            raise PacketFormatError(f"unsupported packet version: {version}")
        if sack_count > MAX_SACK_BLOCKS:
            raise PacketFormatError(f"at most {MAX_SACK_BLOCKS} SACK blocks are supported")
        header_length = HEADER_SIZE + sack_count * _SACK_BLOCK.size
        if header_length > len(raw):
            raise PacketFormatError("packet ends before its SACK blocks")
        if payload_length > MAX_PAYLOAD:
            raise PacketFormatError(f"payload exceeds {MAX_PAYLOAD} bytes")
        if len(raw) != header_length + payload_length:
            raise PacketFormatError("payload length does not match datagram length")

        checksum_header = bytearray(raw[:HEADER_SIZE])
        struct.pack_into("!I", checksum_header, _CHECKSUM_OFFSET, 0)
        checksum_data = bytes(checksum_header) + raw[HEADER_SIZE:]
        calculated = zlib.crc32(checksum_data) & 0xFFFFFFFF
        if checksum != calculated:
            raise ChecksumError(
                f"CRC32 mismatch: expected 0x{checksum:08x}, "
                f"calculated 0x{calculated:08x}"
            )

        blocks = tuple(
            SACKBlock(*_SACK_BLOCK.unpack_from(raw, offset))
            for offset in range(
                HEADER_SIZE, header_length, _SACK_BLOCK.size
            )
        )
        payload = raw[header_length:]
        return cls(
            flags=flags,
            connection_id=connection_id,
            sequence=sequence,
            acknowledgement=acknowledgement,
            window=window,
            payload=payload,
            timestamp=timestamp,
            timestamp_echo=timestamp_echo,
            sack_blocks=blocks,
        )

    @classmethod
    def unpack(cls, data: bytes | bytearray | memoryview) -> "Packet":
        """Alias for :meth:`decode`."""

        return cls.decode(data)


def encode(packet: Packet) -> bytes:
    """Serialize a :class:`Packet`."""

    return packet.encode()


def decode(data: bytes | bytearray | memoryview) -> Packet:
    """Deserialize a :class:`Packet`."""

    return Packet.decode(data)


def _validate_uint8(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
        raise PacketError(f"{name} must be an unsigned 8-bit integer")


def _validate_uint16(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
        raise PacketError(f"{name} must be an unsigned 16-bit integer")


def _validate_uint32(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
        raise PacketError(f"{name} must be an unsigned 32-bit integer")
