#!/usr/bin/env python3
"""Waybar module for an ECHONET Lite home air conditioner / heat pump.

    heatpump-status.py            waybar JSON on stdout
    heatpump-status.py toggle     power on/off
    heatpump-status.py warmer     setpoint +1
    heatpump-status.py cooler     setpoint -1
    heatpump-status.py mode heat|cool|dry|fan|auto
    heatpump-status.py fanup      fan speed +1
    heatpump-status.py fandown    fan speed -1
    heatpump-status.py fancycle   auto -> 1 -> ... -> 8 -> auto
    heatpump-status.py fan 1-8|auto
    heatpump-status.py settemp N  setpoint to N degrees
    heatpump-status.py calibrate  find the unit's real top fan speed
    heatpump-status.py state      full state as JSON (drives the eww panel)
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
POKE = os.path.join(RUNTIME, "waybar-heatpump.poke")
CONF = os.path.expanduser("~/.config/waybar/heatpump.conf")

CACHE_TTL = float(os.environ.get("HEATPUMP_CACHE_TTL", "20"))
WAYBAR_SIGNAL = 12

EPC_POWER, EPC_MODE, EPC_SETPOINT = 0x80, 0xB0, 0xB3
EPC_ROOM, EPC_OUTDOOR, EPC_FAN = 0xBB, 0xBE, 0xA0
EPC_FAULT, EPC_ENERGY = 0x88, 0x85

MODES = {0x41: "auto", 0x42: "cool", 0x43: "heat", 0x44: "dry", 0x45: "fan"}
MODE_BYTES = {v: k for k, v in MODES.items()}

SETPOINT_MIN, SETPOINT_MAX = 16, 31

# EPC 0xA0 encodes fan levels 1-8 as 0x31-0x38, but a given unit only
# implements some of them and silently ignores a set above its ceiling (this
# Mitsubishi tops out at 6). `calibrate` finds the real ceiling and writes it
# to the config file; until then assume the spec maximum.
FAN_MIN, FAN_SPEC_MAX = 1, 8
FAN_AUTO = 0x41


# --- helpers ---------------------------------------------------------------

def read_conf():
    """`key = value` lines. A bare line is taken as the IP, so the simplest
    possible config file is just an address."""
    conf = {}
    try:
        with open(CONF) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip()
                else:
                    conf.setdefault("ip", line)
    except OSError:
        pass
    return conf


def write_conf(**fields):
    conf = read_conf()
    conf.update({k: str(v) for k, v in fields.items()})
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w") as f:
        f.write("# waybar-heatpump device config\n")
        for k, v in sorted(conf.items()):
            f.write("%s = %s\n" % (k, v))


def fan_max():
    v = os.environ.get("HEATPUMP_FAN_MAX") or read_conf().get("fan_max")
    try:
        return max(FAN_MIN, min(FAN_SPEC_MAX, int(v)))
    except (TypeError, ValueError):
        return FAN_SPEC_MAX


def device_ip():
    ip = os.environ.get("HEATPUMP_IP") or read_conf().get("ip")
    if ip:
        return ip
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
    """Returns "auto", an int level 1-8, or None if unsupported/unknown."""
    if not raw:
        return None
    if raw[0] == 0x41:
        return "auto"
    if FAN_MIN <= raw[0] - 0x30 <= FAN_SPEC_MAX:
        return raw[0] - 0x30
    return None


def fan_text(value):
    if value is None:
        return None
    return "auto" if value == "auto" else "%d/%d" % (value, fan_max())


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


def patch_cache(**fields):
    """Record what we just told the device, rather than dropping the cache.

    The unit takes a second or two to reflect a write in its own properties,
    so a re-read straight after a set returns the *old* value. Without this,
    holding a scroll would keep reading the stale setpoint and stop
    advancing. Writing our intent through also makes the bar update instantly.
    """
    try:
        with open(CACHE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return
    st.update(fields)
    st["ts"] = time.time()
    try:
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def nudge_waybar():
    """Push a redraw to both front ends after a control action.

    waybar takes a real-time signal; the eww panel watches POKE's mtime,
    because its listener is a plain pipe with nowhere to send a signal to.
    """
    subprocess.run(["pkill", "-RTMIN+%d" % WAYBAR_SIGNAL, "waybar"],
                   stderr=subprocess.DEVNULL)
    try:
        with open(POKE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


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

    lines = ["Power      %s" % ("on" if st["on"] else "off")]
    if st["mode"]:
        lines.append("Mode       %s" % st["mode"])
    if st["setpoint"] is not None:
        lines.append("Target     %d°C" % st["setpoint"])
    if st["room"] is not None:
        lines.append("Room       %d°C" % st["room"])
    if st["outdoor"] is not None:
        lines.append("Outside    %d°C" % st["outdoor"])
    if st["fan"] is not None:
        lines.append("Fan        %s" % fan_text(st["fan"]))
    if st["fault"]:
        lines.append("")
        lines.append("FAULT reported")
    lines += ["", "click: panel   right: power"]
    tooltip = "\n".join(lines)

    if st["fault"]:
        emit("hp fault", "fault", tooltip)
    elif not st["on"]:
        emit("hp off", "off", tooltip)
    else:
        room = "%d°C" % st["room"] if st["room"] is not None else "on"
        emit("hp %s" % room, st["mode"] or "on", tooltip)


def state_json(max_age=CACHE_TTL):
    """Flat state for the eww panel, with the display work already done.

    Yuck can't do much arithmetic or formatting, so anything the panel needs
    as a string or a list is computed here rather than in the widget.
    """
    ip = device_ip()
    fmax = fan_max()
    if not ip:
        return {"ok": False, "reason": "no device found",
                "fanbars": [], "modes": []}
    try:
        st = cached_state(ip, max_age)
    except el.EchonetError as e:
        return {"ok": False, "reason": str(e), "fanbars": [], "modes": []}

    return {
        "ok": True,
        "ip": ip,
        "on": st["on"],
        "power_text": "on" if st["on"] else "off",
        "mode": st["mode"] or "",
        "modes": [{"name": m, "active": st["mode"] == m}
                  for m in ("heat", "cool", "dry", "fan")],
        "setpoint": st["setpoint"],
        "setpoint_text": ("%d°" % st["setpoint"]
                          if st["setpoint"] is not None else "--"),
        # The editable field holds the bare number - a degree sign in there
        # would just be something to delete before typing.
        "setpoint_input": ("%d" % st["setpoint"]
                           if st["setpoint"] is not None else ""),
        "room_temp": "%d°" % st["room"] if st["room"] is not None else "--",
        "outdoor_temp": ("%d°" % st["outdoor"]
                         if st["outdoor"] is not None else "--"),
        "fan": st["fan"] if st["fan"] is not None else "",
        "fan_text": fan_text(st["fan"]) or "--",
        "fan_auto": st["fan"] == "auto",
        # One entry per selectable speed; `filled` drives the bar graph look,
        # so auto leaves every segment empty.
        "fanbars": [{"level": n,
                     "filled": st["fan"] != "auto" and st["fan"] is not None
                               and n <= st["fan"]}
                    for n in range(FAN_MIN, fmax + 1)],
        "fault": st["fault"],
    }


# --- control ---------------------------------------------------------------

def toggle():
    ip = device_ip()
    st = cached_state(ip)
    new = not st["on"]
    el.set_prop(ip, EPC_POWER, bytes([0x30 if new else 0x31]))
    patch_cache(on=new)
    nudge_waybar()


def bump(delta):
    ip = device_ip()
    st = cached_state(ip)
    if st["setpoint"] is None:
        sys.exit("device did not report a setpoint")
    new = max(SETPOINT_MIN, min(SETPOINT_MAX, st["setpoint"] + delta))
    if new != st["setpoint"]:
        el.set_prop(ip, EPC_SETPOINT, bytes([new]))
        patch_cache(setpoint=new)
        nudge_waybar()


def write_fan(ip, value):
    """value is "auto" or an int level."""
    raw = FAN_AUTO if value == "auto" else 0x30 + value
    el.set_prop(ip, EPC_FAN, bytes([raw]))
    patch_cache(fan=value)
    nudge_waybar()


def set_fan(arg):
    if arg == "auto":
        value = "auto"
    else:
        try:
            value = int(arg)
        except ValueError:
            sys.exit("fan takes %d-%d or 'auto'" % (FAN_MIN, fan_max()))
        if not FAN_MIN <= value <= fan_max():
            sys.exit("fan level must be %d-%d" % (FAN_MIN, fan_max()))
    write_fan(device_ip(), value)


def bump_fan(delta):
    """Auto counts as the step below level 1, so nudging up off auto lands on
    the slowest manual speed rather than jumping to the middle of the range."""
    ip = device_ip()
    current = cached_state(ip)["fan"]
    if current is None:
        sys.exit("device did not report a fan speed")
    if current == "auto":
        new = FAN_MIN if delta > 0 else "auto"
    else:
        level = current + delta
        new = "auto" if level < FAN_MIN else min(level, fan_max())
    if new != current:
        write_fan(ip, new)


def cycle_fan():
    ip = device_ip()
    current = cached_state(ip)["fan"]
    if current is None:
        sys.exit("device did not report a fan speed")
    if current == "auto":
        new = FAN_MIN
    else:
        new = "auto" if current >= fan_max() else current + 1
    write_fan(ip, new)


def set_temp(arg):
    try:
        value = int(arg)
    except ValueError:
        sys.exit("settemp takes a temperature in °C")
    if not SETPOINT_MIN <= value <= SETPOINT_MAX:
        sys.exit("setpoint must be %d-%d" % (SETPOINT_MIN, SETPOINT_MAX))
    el.set_prop(device_ip(), EPC_SETPOINT, bytes([value]))
    patch_cache(setpoint=value)
    nudge_waybar()


def set_mode(name):
    if name not in MODE_BYTES:
        sys.exit("mode must be one of: %s" % ", ".join(MODE_BYTES))
    el.set_prop(device_ip(), EPC_MODE, bytes([MODE_BYTES[name]]))
    patch_cache(mode=name)
    nudge_waybar()


def calibrate():
    """Find the highest fan level this unit actually honours.

    A set above the ceiling is accepted at the protocol level - the device
    returns Set_Res, not Set_SNA - and then quietly ignored, so the only way
    to tell is to write a value and read it back. Walks down from the spec
    maximum, restores the original speed, and records the result in the
    config file.
    """
    ip = device_ip()
    if not ip:
        sys.exit("no heat pump found")
    original = fan(el.get(ip, [EPC_FAN]).get(EPC_FAN))
    if original is None:
        sys.exit("device did not report a fan speed")
    print("current fan: %s" % ("auto" if original == "auto" else original))

    ceiling = FAN_MIN
    for level in range(FAN_SPEC_MAX, FAN_MIN - 1, -1):
        el.set_prop(ip, EPC_FAN, bytes([0x30 + level]))
        time.sleep(3)       # the unit takes a beat to apply a change
        if fan(el.get(ip, [EPC_FAN]).get(EPC_FAN)) == level:
            ceiling = level
            print("  level %d: honoured" % level)
            break
        print("  level %d: ignored" % level)

    raw = FAN_AUTO if original == "auto" else 0x30 + original
    el.set_prop(ip, EPC_FAN, bytes([raw]))
    invalidate_cache()
    write_conf(ip=ip, fan_max=ceiling)
    print("\nfan_max = %d, written to %s" % (ceiling, CONF))


def invalidate_cache():
    try:
        os.unlink(CACHE)
    except OSError:
        pass


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
        return "fan: %s" % (fan_text(fan(raw)) or "?")
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
        elif cmd == "fan":
            set_fan(sys.argv[2] if len(sys.argv) > 2 else "")
        elif cmd == "fanup":
            bump_fan(+1)
        elif cmd == "fandown":
            bump_fan(-1)
        elif cmd == "fancycle":
            cycle_fan()
        elif cmd == "calibrate":
            calibrate()
        elif cmd == "state":
            print(json.dumps(state_json()))
        elif cmd == "settemp":
            set_temp(sys.argv[2] if len(sys.argv) > 2 else "")
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
