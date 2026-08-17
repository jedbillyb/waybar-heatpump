# waybar-heatpump

Waybar module for a heat pump / air conditioner that speaks **ECHONET Lite**
on the local network. Reads room and outdoor temperature, mode, setpoint, fan
speed, fault flag and lifetime energy, and can control power, setpoint and
mode from the bar.

Written against a Mitsubishi Electric unit (manufacturer code `0x000006`,
EOJ `013001`, "home air conditioner"), but nothing in it is vendor-specific —
it only uses standard ECHONET Lite properties and reads the device's own
property map, so it should work with any ECHONET Lite aircon.

```
hp 22°C
```

Tooltip:

```
Heat pump  192.168.68.50

Power      on
Mode       heat
Setpoint   31°C
Room       22°C
Outdoor    5°C
Fan        6/6
Lifetime   1586.8 kWh

click: power   scroll: setpoint
right: fan     middle: fan auto
```

## Usage

```
heatpump-status.py            waybar JSON on stdout
heatpump-status.py toggle     power on/off
heatpump-status.py warmer     setpoint +1
heatpump-status.py cooler     setpoint -1
heatpump-status.py mode heat|cool|dry|fan|auto
heatpump-status.py fanup      fan speed +1
heatpump-status.py fandown    fan speed -1
heatpump-status.py fancycle   auto -> 1 -> ... -> max -> auto
heatpump-status.py fan 1-8|auto
heatpump-status.py calibrate  find the unit's real top fan speed
heatpump-status.py dump       every readable property, decoded
heatpump-status.py discover   find ECHONET Lite nodes on the LAN
```

No dependencies beyond Python 3.

## Finding the device

In order of precedence:

1. `$HEATPUMP_IP`
2. a single line in `~/.config/waybar/heatpump.conf`
3. multicast discovery (`224.0.23.0:3610`), cached in `$XDG_RUNTIME_DIR` for
   an hour

Discovery asks every node for property `0xD6` (self-node instance list) and
picks the first one advertising an EOJ in class `0130`. `discover` prints
what it finds, if you'd rather pin the address in the config file.

## Waybar wiring

```json
"custom/heatpump": {
    "exec": "~/.config/waybar/heatpump-status.py",
    "return-type": "json",
    "interval": 30,
    "signal": 12,
    "on-click": "~/.config/waybar/heatpump-status.py toggle",
    "on-scroll-up": "~/.config/waybar/heatpump-status.py warmer",
    "on-scroll-down": "~/.config/waybar/heatpump-status.py cooler",
    "on-click-right": "~/.config/waybar/heatpump-status.py fancycle",
    "on-click-middle": "~/.config/waybar/heatpump-status.py fan auto",
    "tooltip": true
}
```

The module emits a CSS class per state — `heat`, `cool`, `dry`, `fan`, `auto`,
`on`, `off`, `fault`, `unreachable` — so the bar can colour by what the unit
is actually doing. Control commands invalidate the cache and push
`SIGRTMIN+12` so the bar updates immediately instead of waiting out the poll
interval.

Left click toggles power, scroll changes the setpoint, right click cycles fan
speed (auto → 1 → … → max → auto), middle click returns the fan to auto.

Setpoint changes are clamped to 16–31°C. The device will happily accept 0–50
over the wire; that range is a guard against a stray scroll setting something
absurd. Note that scroll bindings on a bar module are easy to trigger by
accident — a scroll gesture that happens to land on the module will walk the
setpoint to one end of that range. If that bothers you, drop the two
`on-scroll-*` lines and drive the setpoint from the CLI.

## Fan speed

ECHONET Lite defines eight fan levels (`0x31`–`0x38` in EPC `0xA0`) plus auto,
but units implement fewer, and **a set above what the unit supports is not
rejected** — it returns `Set_Res` like any other write and is then silently
ignored. The Mitsubishi this was written against tops out at 6.

So the ceiling has to be measured, not assumed:

```
heatpump-status.py calibrate
```

That walks down from level 8, writing and reading back each one until it
finds the highest that sticks, restores the fan to where it was, and records
`fan_max` in `~/.config/waybar/heatpump.conf`. Run it once per unit. Without
it the module assumes 8 and the top couple of steps will appear to do
nothing.

`fanup`/`fandown` step through the levels and treat auto as the step below
level 1, so nudging up off auto lands on the slowest manual speed rather than
jumping into the middle of the range.

## Config file

`~/.config/waybar/heatpump.conf`, `key = value` per line. A bare line is read
as the IP, so the minimal file is just an address.

```
ip = 192.168.68.50
fan_max = 6
```

`$HEATPUMP_IP` and `$HEATPUMP_FAN_MAX` override the file.

## Protocol notes

Two things about ECHONET Lite cost real debugging time here, both handled in
`echonetlite.py`:

**Replies come back to port 3610, not to the source port.** A conventional
"send from an ephemeral port and read the answer" client just times out. The
socket has to bind 3610 itself.

**That makes concurrent polling unsafe.** Two processes bound to 3610 with
`SO_REUSEADDR` will steal each other's datagrams — only one socket receives
any given packet. So reads go through an flock plus a short-lived cache
(`$XDG_RUNTIME_DIR/waybar-heatpump.json`, 20s, override with
`$HEATPUMP_CACHE_TTL`), and every response is matched on its transaction ID
before being accepted.

**Property maps have two encodings.** Under 16 properties they're a plain
list; at 16 or more they switch to a transposed bitmap where bit `row` of
byte `col` means property `((row+8)<<4)|col`. `decode_property_map` handles
both.

### Properties used

| EPC  | Meaning                    | Notes |
|------|----------------------------|-------|
| 0x80 | Power                      | `0x30` on, `0x31` off |
| 0xB0 | Mode                       | `0x41` auto, `0x42` cool, `0x43` heat, `0x44` dry, `0x45` fan |
| 0xB3 | Setpoint °C                | `0x00`–`0x32`, else undefined |
| 0xBB | Measured room temp °C      | signed byte |
| 0xBE | Measured outdoor temp °C   | signed byte |
| 0xA0 | Fan speed                  | `0x41` auto, `0x31`–`0x38` levels 1–8 |
| 0x88 | Fault status               | `0x41` fault, `0x42` no fault |
| 0x85 | Cumulative energy          | 4 bytes, units of 0.001 kWh |

`dump` prints these plus everything else the unit says it can return, which
is the quickest way to see what a different model exposes. Note that not
every aircon reports outdoor temperature (`0xBE`) or energy (`0x85`); the
module omits missing fields from the tooltip rather than showing zeroes.

## Security

ECHONET Lite as specified has no authentication whatsoever — anything on the
same L2 network can read and control the unit. That is what makes this module
possible, and it is also worth knowing about: keep the device off any network
you don't trust, and don't expose UDP 3610 to the internet.
