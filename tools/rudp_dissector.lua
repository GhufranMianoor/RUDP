-- Wireshark dissector for the CN-project Reliable UDP protocol (UDP port 9000).
--
-- Display filter prefix: rudpcn   (e.g.  rudpcn.flags.syn == 1,  rudpcn.sack_count > 0)
--
-- The name is deliberately not "rudp": Wireshark ships its own "Reliable UDP"
-- dissector, and registering a second protocol with that name or description
-- makes this script fail to load.
--
-- Header (32 bytes, network order), then SACK blocks, then payload:
--   0  version u8      1  flags u8       2  connection id u16
--   4  sequence u32    8  ack u32       12  window u16        14  payload len u16
--  16  timestamp u32  20  ts echo u32   24  CRC32 u32         28  SACK count u8
--  SACK block = start u32, end u32 -- both INCLUSIVE sequence numbers.
-- Flags: SYN 0x01  ACK 0x02  FIN 0x04  DATA 0x08  RST 0x10

local rudp = Proto("rudpcn", "CN Project Reliable UDP")

local f = {
    version        = ProtoField.uint8("rudpcn.version", "Version", base.DEC),
    flags          = ProtoField.uint8("rudpcn.flags", "Flags", base.HEX),
    flag_syn       = ProtoField.bool("rudpcn.flags.syn", "SYN", 8, nil, 0x01),
    flag_ack       = ProtoField.bool("rudpcn.flags.ack", "ACK", 8, nil, 0x02),
    flag_fin       = ProtoField.bool("rudpcn.flags.fin", "FIN", 8, nil, 0x04),
    flag_data      = ProtoField.bool("rudpcn.flags.data", "DATA", 8, nil, 0x08),
    flag_rst       = ProtoField.bool("rudpcn.flags.rst", "RST", 8, nil, 0x10),
    flag_names     = ProtoField.string("rudpcn.flag_names", "Flag names"),
    connection_id  = ProtoField.uint16("rudpcn.connection_id", "Connection ID", base.DEC),
    sequence       = ProtoField.uint32("rudpcn.sequence", "Sequence", base.DEC),
    acknowledgement = ProtoField.uint32("rudpcn.acknowledgement", "Acknowledgement (next expected)", base.DEC),
    window         = ProtoField.uint16("rudpcn.window", "Receive window (packets)", base.DEC),
    payload_length = ProtoField.uint16("rudpcn.payload_length", "Payload length", base.DEC),
    timestamp      = ProtoField.uint32("rudpcn.timestamp", "Timestamp (ms)", base.DEC),
    timestamp_echo = ProtoField.uint32("rudpcn.timestamp_echo", "Timestamp echo (ms)", base.DEC),
    checksum       = ProtoField.uint32("rudpcn.checksum", "CRC32", base.HEX),
    sack_count     = ProtoField.uint8("rudpcn.sack_count", "SACK count", base.DEC),
    sack_start     = ProtoField.uint32("rudpcn.sack.start", "SACK start (inclusive)", base.DEC),
    sack_end       = ProtoField.uint32("rudpcn.sack.end", "SACK end (inclusive)", base.DEC),
    sack_ranges    = ProtoField.string("rudpcn.sack.ranges", "SACK ranges"),
    payload        = ProtoField.bytes("rudpcn.payload", "Payload"),
}
local fields = {}
for _, field in pairs(f) do table.insert(fields, field) end
rudp.fields = fields

local FLAGS = { {0x01, "SYN"}, {0x02, "ACK"}, {0x04, "FIN"}, {0x08, "DATA"}, {0x10, "RST"} }

local function flag_string(value)
    local names = {}
    for _, item in ipairs(FLAGS) do
        if bit32.band(value, item[1]) ~= 0 then table.insert(names, item[2]) end
    end
    if #names == 0 then return "none" end
    return table.concat(names, "+")
end

local function dissect(buffer, pinfo, tree)
    local available = buffer:len()
    if available < 32 or buffer(0, 1):uint() ~= 1 then
        return 0
    end
    pinfo.cols.protocol = "RUDPCN"

    local flags = buffer(1, 1):uint()
    local names = flag_string(flags)
    local sequence = buffer(4, 4):uint()
    local acknowledgement = buffer(8, 4):uint()
    local payload_length = buffer(14, 2):uint()
    local sack_count = buffer(28, 1):uint()

    local root = tree:add(rudp, buffer(), "CN Project Reliable UDP: " .. names)
    root:add(f.version, buffer(0, 1))
    local flags_tree = root:add(f.flags, buffer(1, 1)):append_text(" (" .. names .. ")")
    flags_tree:add(f.flag_syn, buffer(1, 1))
    flags_tree:add(f.flag_ack, buffer(1, 1))
    flags_tree:add(f.flag_fin, buffer(1, 1))
    flags_tree:add(f.flag_data, buffer(1, 1))
    flags_tree:add(f.flag_rst, buffer(1, 1))
    root:add(f.flag_names, buffer(1, 1), names):set_generated()
    root:add(f.connection_id, buffer(2, 2))
    root:add(f.sequence, buffer(4, 4))
    root:add(f.acknowledgement, buffer(8, 4))
    root:add(f.window, buffer(12, 2))
    root:add(f.payload_length, buffer(14, 2))
    root:add(f.timestamp, buffer(16, 4))
    root:add(f.timestamp_echo, buffer(20, 4))
    root:add(f.checksum, buffer(24, 4))
    root:add(f.sack_count, buffer(28, 1))

    local offset = 32
    local ranges = {}
    for i = 1, sack_count do
        if offset + 8 > available then break end
        local first = buffer(offset, 4):uint()
        local last = buffer(offset + 4, 4):uint()
        local block = root:add(buffer(offset, 8),
            string.format("SACK block %d: %d-%d (inclusive, %d packets)", i, first, last, last - first + 1))
        block:add(f.sack_start, buffer(offset, 4))
        block:add(f.sack_end, buffer(offset + 4, 4))
        table.insert(ranges, string.format("%d-%d", first, last))
        offset = offset + 8
    end
    if #ranges > 0 then
        root:add(f.sack_ranges, buffer(32, offset - 32), table.concat(ranges, ",")):set_generated()
    end

    -- Captures may be snaplen-truncated; only add the payload bytes that exist.
    if payload_length > 0 and offset < available then
        root:add(f.payload, buffer(offset, math.min(payload_length, available - offset)))
    end

    local info = names
    if bit32.band(flags, 0x08) ~= 0 then
        info = string.format("DATA seq=%d len=%d", sequence, payload_length)
    elseif bit32.band(flags, 0x04) ~= 0 then
        info = string.format("%s seq=%d ack=%d", names, sequence, acknowledgement)
    elseif bit32.band(flags, 0x02) ~= 0 and bit32.band(flags, 0x01) == 0 then
        info = string.format("ACK ack=%d win=%d", acknowledgement, buffer(12, 2):uint())
    elseif bit32.band(flags, 0x01) ~= 0 then
        info = string.format("%s seq=%d ack=%d", names, sequence, acknowledgement)
    end
    if #ranges > 0 then
        info = info .. " SACK[" .. table.concat(ranges, ",") .. "]"
    end
    pinfo.cols.info = info
    return available
end

function rudp.dissector(buffer, pinfo, tree)
    return dissect(buffer, pinfo, tree)
end

DissectorTable.get("udp.port"):add(9000, rudp)
