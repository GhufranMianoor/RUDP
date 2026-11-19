-- Wireshark dissector for the RUDP protocol on UDP port 9000.

local rudp = Proto("rudp", "Reliable UDP")

local f = {
    version = ProtoField.uint8("rudp.version", "Version", base.DEC),
    flags = ProtoField.uint8("rudp.flags", "Flags", base.HEX),
    connection_id = ProtoField.uint16("rudp.connection_id", "Connection ID", base.DEC),
    sequence = ProtoField.uint32("rudp.sequence", "Sequence", base.DEC),
    acknowledgement = ProtoField.uint32("rudp.acknowledgement", "Acknowledgement", base.DEC),
    window = ProtoField.uint16("rudp.window", "Window", base.DEC),
    payload_length = ProtoField.uint16("rudp.payload_length", "Payload length", base.DEC),
    timestamp = ProtoField.uint32("rudp.timestamp", "Timestamp (ms)", base.DEC),
    timestamp_echo = ProtoField.uint32("rudp.timestamp_echo", "Timestamp echo (ms)", base.DEC),
    checksum = ProtoField.uint32("rudp.checksum", "CRC32", base.HEX),
    sack_count = ProtoField.uint8("rudp.sack_count", "SACK count", base.DEC),
    sack_start = ProtoField.uint32("rudp.sack.start", "SACK start", base.DEC),
    sack_end = ProtoField.uint32("rudp.sack.end", "SACK end", base.DEC),
    payload = ProtoField.bytes("rudp.payload", "Payload"),
}
rudp.fields = f

local function dissect(buffer, pinfo, tree)
    if buffer:len() < 32 or buffer(0, 1):uint() ~= 1 then
        return 0
    end
    pinfo.cols.protocol = "RUDP"
    local root = tree:add(rudp, buffer(), "Reliable UDP")
    root:add(f.version, buffer(0, 1))
    root:add(f.flags, buffer(1, 1))
    root:add(f.connection_id, buffer(2, 2))
    root:add(f.sequence, buffer(4, 4))
    root:add(f.acknowledgement, buffer(8, 4))
    root:add(f.window, buffer(12, 2))
    local payload_length = buffer(14, 2):uint()
    root:add(f.payload_length, buffer(14, 2))
    root:add(f.timestamp, buffer(16, 4))
    root:add(f.timestamp_echo, buffer(20, 4))
    root:add(f.checksum, buffer(24, 4))
    local sack_count = buffer(28, 1):uint()
    root:add(f.sack_count, buffer(28, 1))
    local offset = 32
    for i = 1, sack_count do
        if offset + 8 > buffer:len() then
            break
        end
        local sack = root:add(rudp, buffer(offset, 8), "SACK block " .. i)
        sack:add(f.sack_start, buffer(offset, 4))
        sack:add(f.sack_end, buffer(offset + 4, 4))
        offset = offset + 8
    end
    if payload_length > 0 and offset + payload_length <= buffer:len() then
        root:add(f.payload, buffer(offset, payload_length))
    end
    return buffer:len()
end

function rudp.dissector(buffer, pinfo, tree)
    return dissect(buffer, pinfo, tree)
end

local udp_table = DissectorTable.get("udp.port")
udp_table:add(9000, rudp)
