"""Minimal ECHONET Lite client, enough to talk to a home air conditioner.

The protocol is UDP/3610. The one thing that trips you up: this heat pump
replies to port 3610 rather than to the ephemeral source port, so we have to
bind 3610 to hear the answer at all. SO_REUSEADDR keeps concurrent callers
from erroring out on the bind, though only one of them will actually receive
a given datagram - see the lock in heatpump-status.py.
"""

import socket
import struct

PORT = 3610
MCAST = "224.0.23.0"

CONTROLLER = "05FF01"   # SEOJ we present as
NODE_PROFILE = "0EF001"
AIRCON = "013001"       # class group 01, class 30, instance 1

ESV_GET = 0x62
ESV_SET = 0x61          # SetC, device confirms
ESV_GET_RES = 0x72
ESV_SET_RES = 0x71
ESV_GET_SNA = 0x52      # partial/failed Get
ESV_SET_SNA = 0x51

_tid = 0


class EchonetError(Exception):
    pass


class Timeout(EchonetError):
    pass


def _next_tid():
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    return _tid


def _socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", PORT))
    except OSError:
        # Someone else holds it exclusively; fall back and hope the device
        # answers our source port. Better than not trying.
        pass
    return s


def _frame(tid, deoj, esv, props):
    """props: list of (epc, payload_bytes)."""
    out = bytearray([0x10, 0x81, tid >> 8, tid & 0xFF])
    out += bytes.fromhex(CONTROLLER)
    out += bytes.fromhex(deoj)
    out += bytes([esv, len(props)])
    for epc, payload in props:
        out += bytes([epc, len(payload)]) + payload
    return bytes(out)


def _parse(data):
    if len(data) < 12 or data[0] != 0x10 or data[1] != 0x81:
        raise EchonetError("not an ECHONET Lite frame")
    tid = (data[2] << 8) | data[3]
    esv = data[10]
    i = 12
    props = {}
    for _ in range(data[11]):
        if i + 2 > len(data):
            break
        epc, pdc = data[i], data[i + 1]
        props[epc] = data[i + 2:i + 2 + pdc]
        i += 2 + pdc
    return tid, esv, props


def request(ip, deoj, esv, props, timeout=3.0):
    tid = _next_tid()
    frame = _frame(tid, deoj, esv, props)
    s = _socket()
    s.settimeout(timeout)
    try:
        s.sendto(frame, (ip, PORT))
        deadline = timeout
        while deadline > 0:
            s.settimeout(deadline)
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                raise Timeout("no reply from %s" % ip)
            if addr[0] != ip or data == frame:
                continue
            rtid, resv, rprops = _parse(data)
            if rtid != tid:
                continue    # a reply to somebody else's poll
            return resv, rprops
        raise Timeout("no reply from %s" % ip)
    finally:
        s.close()


def get(ip, epcs, deoj=AIRCON, timeout=3.0):
    """Read properties. Returns {epc: bytes}; unsupported EPCs come back empty."""
    esv, props = request(ip, deoj, ESV_GET, [(e, b"") for e in epcs], timeout)
    if esv not in (ESV_GET_RES, ESV_GET_SNA):
        raise EchonetError("unexpected ESV 0x%02X" % esv)
    return {k: v for k, v in props.items() if v}


def set_prop(ip, epc, payload, deoj=AIRCON, timeout=3.0):
    esv, _ = request(ip, deoj, ESV_SET, [(epc, payload)], timeout)
    if esv == ESV_SET_SNA:
        raise EchonetError("device rejected set of EPC 0x%02X" % epc)
    if esv != ESV_SET_RES:
        raise EchonetError("unexpected ESV 0x%02X" % esv)


def discover(timeout=3.0):
    """Multicast for node profiles. Returns {ip: [eoj_hex, ...]}."""
    tid = _next_tid()
    frame = _frame(tid, NODE_PROFILE, ESV_GET, [(0xD6, b"")])
    s = _socket()
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    s.settimeout(0.5)
    found = {}
    try:
        s.sendto(frame, (MCAST, PORT))
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            if data == frame:
                continue
            try:
                _, esv, props = _parse(data)
            except EchonetError:
                continue
            if esv != ESV_GET_RES or 0xD6 not in props:
                continue
            raw = props[0xD6]
            if not raw:
                continue
            eojs = [raw[1 + i * 3:4 + i * 3].hex().upper()
                    for i in range(raw[0])]
            found[addr[0]] = eojs
    finally:
        s.close()
    return found


def decode_property_map(raw):
    """ECHONET property maps use two encodings depending on how many
    properties there are: a plain list under 16, a transposed bitmap at 16+."""
    if not raw:
        return []
    n = raw[0]
    if n < 16:
        return sorted(raw[1:1 + n])
    props = []
    for col in range(16):
        byte = raw[1 + col]
        for row in range(8):
            if byte >> row & 1:
                props.append(((row + 8) << 4) | col)
    return sorted(props)
