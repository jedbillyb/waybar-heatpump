#!/usr/bin/env python3
"""Waybar module for an ECHONET Lite home air conditioner / heat pump.

    heatpump-status.py            waybar JSON on stdout
    heatpump-status.py toggle     power on/off
    heatpump-status.py warmer     setpoint +1
    heatpump-status.py cooler     setpoint -1
    heatpump-status.py mode heat|cool|dry|fan|auto
    heatpump-status.py dump       every readable property, decoded
    heatpump-status.py discover   find ECHONET nodes on the LAN

Device address comes from $HEATPUMP_IP, else ~/.config/waybar/heatpump.conf,
else multicast discovery (result cached).
"""

import fcntl
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import echonetlite as el

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
CACHE = os.path.join(RUNTIME, "waybar-heatpump.json")
IPCACHE = os.path.join(RUNTIME, "waybar-heatpump.ip")
LOCK = os.path.join(RUNTIME, "waybar-heatpump.lock")
CONF = os.path.expanduser("~/.config/waybar/heatpump.conf")

CACHE_TTL = float(os.environ.get("HEATPUMP_CACHE_TTL", "20"))
WAYBAR_SIGNAL = 12

EPC_POWER, EPC_MODE, EPC_SETPOINT = 0x80, 0xB0, 0xB3
EPC_ROOM, EPC_OUTDOOR, EPC_FAN = 0xBB, 0xBE, 0xA0
EPC_FAULT, EPC_ENERGY = 0x88, 0x85

MODES = {0x41: "auto", 0x42: "cool", 0x43: "heat", 0x44: "dry", 0x45: "fan"}
MODE_BYTES = {v: k for k, v in MODES.items()}

SETPOINT_MIN, SETPOINT_MAX = 16, 31


# --- helpers ---------------------------------------------------------------

def device_ip():
    ip = os.environ.get("HEATPUMP_IP")
    if ip:
        return ip
    try:
        with open(CONF) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    try:
        if time.time() - os.path.getmtime(IPCACHE) < 3600:
            with open(IPCACHE) as f:
                return f.read().strip()
    except OSError:
        pass
    for ip, eojs in el.discover(timeout=3.0).items():
        if any(e.startswith("0130") for e in eojs):
            try:
                with open(IPCACHE, "w") as f:
                    f.write(ip)
            except OSError:
                pass
            return ip
    return None


def temp(raw):
    """Measured temperature: signed byte, with a couple of sentinel values."""
    if not raw:
        return None
    v = raw[0]
    if v in (0x7E, 0x80, 0xFD, 0xFE, 0xFF):
        return None
    return v - 256 if v > 127 else v


def setpoint(raw):
    if not raw or raw[0] > 0x32:
        return None
    return raw[0]


def fan(raw):
    if not raw:
        return None
    if raw[0] == 0x41:
        return "auto"
    if 0x31 <= raw[0] <= 0x38:
        return "%d/8" % (raw[0] - 0x30)
    return None


def read_state(ip):
    props = el.get(ip, [EPC_POWER, EPC_MODE, EPC_SETPOINT, EPC_ROOM,
                        EPC_OUTDOOR, EPC_FAN, EPC_FAULT, EPC_ENERGY])
    energy = None
    if len(props.get(EPC_ENERGY, b"")) == 4:
        energy = int.from_bytes(props[EPC_ENERGY], "big") * 0.001
    return {
        "on": props.get(EPC_POWER, b"\x31")[0] == 0x30,
        "mode": MODES.get(props.get(EPC_MODE, b"\x00")[0]),
        "setpoint": setpoint(props.get(EPC_SETPOINT)),
        "room": temp(props.get(EPC_ROOM)),
        "outdoor": temp(props.get(EPC_OUTDOOR)),
        "fan": fan(props.get(EPC_FAN)),
        "fault": props.get(EPC_FAULT, b"\x42")[0] == 0x41,
        "energy_kwh": energy,
        "ts": time.time(),
    }


def cached_state(ip, max_age=CACHE_TTL):
    """One poller at a time - the reply lands on port 3610, so concurrent
    reads would steal each other's datagrams."""
    try:
        with open(CACHE) as f:
            st = json.load(f)
        if time.time() - st["ts"] < max_age:
            return st
    except (OSError, ValueError, KeyError):
        pass

    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:    # somebody may have refreshed it while we waited
            with open(CACHE) as f:
                st = json.load(f)
            if time.time() - st["ts"] < max_age:
                return st
        except (OSError, ValueError, KeyError):
            pass
        st = read_state(ip)
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, CACHE)
        return st
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


def invalidate():
    try:
        os.unlink(CACHE)
    except OSError:
        pass


def nudge_waybar():
    subprocess.run(["pkill", "-RTMIN+%d" % WAYBAR_SIGNAL, "waybar"],
                   stderr=subprocess.DEVNULL)


# --- waybar output ---------------------------------------------------------

def emit(text, cls, tooltip=""):
    print(json.dumps({"text": text, "class": cls, "tooltip": tooltip}))


def status():
    ip = device_ip()
    if not ip:
        emit("hp --", "unreachable", "No ECHONET Lite heat pump found")
        return
    try:
        st = cached_state(ip)
    except el.EchonetError as e:
        emit("hp --", "unreachable", "%s: %s" % (ip, e))
        return

    lines = ["Heat pump  %s" % ip, ""]
    lines.append("Power      %s" % ("on" if st["on"] else "off"))
    if st["mode"]:
        lines.append("Mode       %s" % st["mode"])
    if st["setpoint"] is not None:
        lines.append("Setpoint   %d°C" % st["setpoint"])
    if st["room"] is not None:
        lines.append("Room       %d°C" % st["room"])
    if st["outdoor"] is not None:
        lines.append("Outdoor    %d°C" % st["outdoor"])
    if st["fan"]:
        lines.append("Fan        %s" % st["fan"])
    if st["energy_kwh"] is not None:
        lines.append("Lifetime   %.1f kWh" % st["energy_kwh"])
    if st["fault"]:
        lines.append("")
        lines.append("FAULT reported")
    lines += ["", "click: power   scroll: setpoint"]
    tooltip = "\n".join(lines)

    if st["fault"]:
        emit("hp fault", "fault", tooltip)
    elif not st["on"]:
        emit("hp off", "off", tooltip)
    else:
        room = "%d°C" % st["room"] if st["room"] is not None else "on"
        emit("hp %s" % room, st["mode"] or "on", tooltip)


# --- control ---------------------------------------------------------------

def toggle():
    ip = device_ip()
    st = cached_state(ip, max_age=0)
    el.set_prop(ip, EPC_POWER, bytes([0x31 if st["on"] else 0x30]))
    invalidate()
    nudge_waybar()


def bump(delta):
    ip = device_ip()
    st = cached_state(ip, max_age=0)
    if st["setpoint"] is None:
        return
    new = max(SETPOINT_MIN, min(SETPOINT_MAX, st["setpoint"] + delta))
    if new != st["setpoint"]:
        el.set_prop(ip, EPC_SETPOINT, bytes([new]))
        invalidate()
        nudge_waybar()


def set_mode(name):
    if name not in MODE_BYTES:
        sys.exit("mode must be one of: %s" % ", ".join(MODE_BYTES))
    el.set_prop(device_ip(), EPC_MODE, bytes([MODE_BYTES[name]]))
    invalidate()
    nudge_waybar()


# --- diagnostics -----------------------------------------------------------

def dump():
    ip = device_ip()
    if not ip:
        sys.exit("no heat pump found")
    print("device %s" % ip)
    maps = el.get(ip, [0x9F, 0x9E])
    readable = el.decode_property_map(maps.get(0x9F, b""))
    writable = set(el.decode_property_map(maps.get(0x9E, b"")))
    print("readable %s" % " ".join("%02X" % p for p in readable))
    print("writable %s" % " ".join("%02X" % p for p in sorted(writable)))
    print()
    for epc in readable:
        if epc in (0x9D, 0x9E, 0x9F):
            continue
        try:
            raw = el.get(ip, [epc]).get(epc, b"")
        except el.EchonetError:
            raw = b""
        print("%02X %-8s %s%s" % (epc, raw.hex() or "-", describe(epc, raw),
                                  "  [rw]" if epc in writable else ""))


def describe(epc, raw):
    if not raw:
        return ""
    if epc == EPC_POWER:
        return "power: %s" % ("on" if raw[0] == 0x30 else "off")
    if epc == EPC_MODE:
        return "mode: %s" % MODES.get(raw[0], "?")
    if epc == EPC_SETPOINT:
        v = setpoint(raw)
        return "setpoint: %s" % ("%d°C" % v if v is not None else "auto")
    if epc in (EPC_ROOM, EPC_OUTDOOR):
        v = temp(raw)
        label = "room" if epc == EPC_ROOM else "outdoor"
        return "%s: %s" % (label, "%d°C" % v if v is not None else "n/a")
    if epc == EPC_FAN:
        return "fan: %s" % (fan(raw) or "?")
    if epc == EPC_FAULT:
        return "fault: %s" % ("YES" if raw[0] == 0x41 else "no")
    if epc == EPC_ENERGY and len(raw) == 4:
        return "energy: %.3f kWh" % (int.from_bytes(raw, "big") * 0.001)
    if epc == 0x8A:
        return "manufacturer: 0x%s" % raw.hex().upper()
    return ""


def discover():
    found = el.discover()
    if not found:
        print("no ECHONET Lite nodes responded")
        return
    for ip, eojs in sorted(found.items()):
        kinds = ["home air conditioner" if e.startswith("0130") else e
                 for e in eojs]
        print("%-15s %s" % (ip, ", ".join(kinds)))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if cmd == "status":
            status()
        elif cmd == "toggle":
            toggle()
        elif cmd == "warmer":
            bump(+1)
        elif cmd == "cooler":
            bump(-1)
        elif cmd == "mode":
            set_mode(sys.argv[2] if len(sys.argv) > 2 else "")
        elif cmd == "dump":
            dump()
        elif cmd == "discover":
            discover()
        else:
            sys.exit(__doc__)
    except el.EchonetError as e:
        if cmd == "status":
            emit("hp --", "unreachable", str(e))
        else:
            sys.exit("error: %s" % e)


if __name__ == "__main__":
    main()
