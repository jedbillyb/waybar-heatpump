# waybar-heatpump

Waybar module for a heat pump / air conditioner that speaks **ECHONET Lite**
on the local network. Reads room and outdoor temperature, mode, setpoint, fan
speed, fault flag and lifetime energy, and can control power, setpoint and
mode from the bar.

Written against a Mitsubishi Electric unit (manufacturer code `0x000006`,
EOJ `013001`, "home air conditioner"), but nothing in it is vendor-specific -
it only uses standard ECHONET Lite properties and reads the device's own
property map, so it should work with any ECHONET Lite aircon.

```
hp 22°C
```

Tooltip:

```
Power      on
Mode       heat
Target     31°C
Room       22°C
Outside    5°C
Fan        6/6

click: panel   right: power
```

## The panel

Left click on the bar module opens an eww control panel; right click toggles
power without opening anything.

It is deliberately not a floating card. It uses waybar's exact background
(`rgba(36,36,36,0.85)`, translucency included), the same font and palette,
square corners, and sits flush against the bar's bottom edge, centred on the
module that opened it.

An accent stripe across the top was tried and dropped: sharing the bar's exact
background and sitting flush against it already reads as attached, and a
coloured stub on top of that just looked stuck on.

The target temperature is click-to-type: click the number, type a value, press
Enter. The steppers are still there for a quick nudge.

```
 heat pump                 on

        −     31°     +
              target
 ────────────────────────────
 room                     22°
 outside                   5°
 ────────────────────────────
 mode    heat  cool  dry  fan
 fan     ▊▊▊▊▊▊  6/6    auto
```

### Positioning

The panel's x position is measured, not hardcoded. The heat pump module is the
leftmost entry in `modules-right`, so it slides sideways whenever anything to
its right changes width - the wifi percentage alone moves it several pixels.

The panel is centred on the module. `panel-position.py` grabs the top strip of
the screen, marks every column carrying a glyph, and takes the content starting
after the widest gap in the right half of the bar - that gap is the space
between the centred clock and the right-hand group.

That finds where the module *starts*. Finding where it ends by looking for the
next gap does not work: the space between `hp off` and the module after it can
be ~12px, which is narrower than the spacing between the words inside `hp off`
itself, so any threshold either splits the module in half or swallows its
neighbour. The width comes from the text instead - waybar renders DejaVu Sans
Mono at 9px and the font is exactly 0.6 em per character, so the module is its
character count times 5.41px wide. The caller passes the module's own bar text
in.

The panel is centred inside its window, so centring the window on the module
centres the panel whatever width its content comes out at.

The glyph threshold matters more than it looks. waybar is translucent, so the
worst case background is a white wallpaper at 15%: `0.15*255 + 0.85*36 = 69`.
The dimmest text on the bar is `#666666` at luminance 102 - which is exactly
what the heat pump module goes when the unit is **off**. A threshold of 130
loses the module in that state and silently reports the next module along, so
the panel opens under the wrong thing precisely when the unit is off. It sits
at 85.

Measuring once at open time is not enough either: the module slides whenever
anything to its right changes width. `heatpump-track.py` re-measures while the
panel is open and moves it, then exits on its own once the panel is gone.

That measurement does not happen before the window opens, though. A grim
capture plus a Pillow import plus a couple of interpreter starts is ~180ms,
and doing it up front made it most of the gap between the click and the panel
appearing. The position is almost always what it was last time, so the panel
opens at the remembered one immediately and the tracker corrects it: the
tracker takes its first measurement straight away rather than after its 1.5s
interval, and writes each result to `$XDG_RUNTIME_DIR/waybar-heatpump.xpos`.
Only the first open after a reboot pays for a measurement, and open time goes
from ~0.36s to ~0.10s.

When the remembered position is wrong it is wrong by a few pixels, because the
module's own text changed width (`hp off` -> `hp 21°C`), and the correction
lands about 200ms after the panel appears.

It runs as its own process for a reason. Repositioning means `eww open` on an
already-open window, which re-renders it and restarts any `deflisten` that
window owns. A tracker living inside the panel's listener therefore kills
itself every time it moves the window and comes back up with no memory of
having done so, which loops forever.

Two approaches that don't work, for the next person:

- **Sampling the bar's background colour.** waybar is translucent, so its
  "background" is the wallpaper underneath and differs at every x. A luminance
  threshold picks out text regardless of what is behind it.
- **Matching the module's own colour.** It changes with the mode, and the greys
  it uses when off are shared with other modules.

### Dismissal

Clicking anywhere outside the panel closes it, and so does Escape. Neither is
something eww can do by itself:

- A layer-shell surface cannot see clicks that land outside it, so
  `heatpump-backdrop` is a full-screen transparent window one layer below the
  panel whose only job is to catch them. It must paint something - a fully
  transparent box takes no pointer events - so it paints black at 1% alpha.
- eww 0.5 has no key events at all. Escape is bound in sway with
  `swaymsg bindsym` when the panel opens and removed with `unbindsym` when it
  closes, so Escape reaches applications normally the rest of the time.

### Keyboard focus, and why there are two windows

eww 0.5 exposes keyboard focus as a single boolean, which maps to layer-shell
`exclusive`. There is no on-demand mode. An exclusive layer surface takes
**every keystroke on the system** for as long as it is open - so a focusable
panel left open means you cannot type in your editor, your terminal, or
anywhere else.

That rules out simply making the panel focusable so its entry works. Instead
there are two windows sharing one widget: `heatpump` is not focusable and is
what you leave open, and `heatpump-edit` is the identical panel with the
target swapped for an entry. Clicking the number swaps one for the other, and
Enter applies the value and swaps back, so the keyboard is held only while you
are actually typing.

If eww is ever updated past 0.6, `:focusable "ondemand"` collapses this back
into a single window.

### Live updates

The listener runs only while the panel is open, so nothing polls the device
the rest of the time. It reads through the same cache and flock as the waybar
module rather than talking to the unit itself - two ECHONET pollers at once
would steal each other's replies. Control actions patch the cache with the
value they sent and touch a poke file the listener watches, so a button press
redraws immediately instead of waiting for the unit to catch up.

## Temperatures

The panel and the tooltip show every temperature this unit reports, which is
all three of them: target, room and outside. Probing the rest of the aircon
class turned up nothing else - no humidity (`0xBA`), no coil or discharge
temperatures, and no instantaneous power (`0x84`).

Lifetime energy (`0x85`) is read but no longer displayed. It is a cumulative
counter that only moves in 0.1 kWh steps, so it says nothing useful about what
the unit is doing right now. It is still handy from `dump` when you want to
know whether the unit is drawing power at all.

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

## Requirements

The bar module needs **nothing beyond Python 3** - no third-party packages,
no ECHONET library. If all you want is the module and its tooltip, that is the
whole dependency list.

The panel adds some, all of them things a Wayland bar setup tends to have
already:

| Needs | For |
|-------|-----|
| [eww](https://github.com/elkowar/eww) 0.5+ | the panel itself |
| `grim` | screenshotting the bar to locate the module |
| Pillow | reading that screenshot |
| sway | binding Escape while the panel is open |

Only the Escape binding is sway-specific. On another wlroots compositor the
panel still opens, tracks and closes on click-off; swap the two `swaymsg`
lines in `heatpump-panel.sh` for your compositor's equivalent to get Escape
back.

## Install

```sh
git clone https://github.com/jedbillyb/waybar-heatpump
cd waybar-heatpump

cp heatpump-status.py echonetlite.py ~/.config/waybar/
cp eww/* ~/.config/eww/
```

Add the include lines to your eww config:

```sh
echo '(include "heatpump.yuck")' >> ~/.config/eww/eww.yuck
echo '@import "heatpump";'       >> ~/.config/eww/eww.scss
```

Then add the module to your waybar config and style it (see below), and run
`heatpump-status.py calibrate` once to find the unit's real top fan speed.

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
    "on-click": "~/.config/eww/heatpump-panel.sh",
    "on-click-right": "~/.config/waybar/heatpump-status.py toggle",
    "tooltip": true
}
```

The module emits a CSS class per state - `heat`, `cool`, `dry`, `fan`, `auto`,
`on`, `off`, `fault`, `unreachable` - so the bar can colour by what the unit
is actually doing. Control commands invalidate the cache and push
`SIGRTMIN+12` so the bar updates immediately instead of waiting out the poll
interval.

```css
#custom-heatpump              { padding: 0 10px; }
#custom-heatpump.off,
#custom-heatpump.unreachable  { color: #666666; }
#custom-heatpump.heat         { color: #e0af68; }
#custom-heatpump.cool         { color: #7dcfff; }
#custom-heatpump.dry,
#custom-heatpump.fan,
#custom-heatpump.auto,
#custom-heatpump.on           { color: #aaaaaa; }
#custom-heatpump.fault        { color: #f7768e; }
```

Give it the same horizontal padding as your other modules. Leaving it off is
worth a specific warning: with waybar's `* { padding: 0 }` reset the module
ends up flush against its neighbour, and because `panel-position.py` reads the
bar as pixels, an unusually tight gap there is exactly the kind of thing that
makes locating the module harder.

Left click opens the panel, right click toggles power.

Scroll bindings (`warmer`/`cooler` on `on-scroll-up`/`on-scroll-down`) were
tried first and removed: a scroll gesture that happens to land on the module
walks the setpoint to the end of its 16-31°C range, which in heat mode reads
as the unit having switched off. The commands still exist for the CLI, they
are just not worth binding to a wheel.

Setpoint changes are clamped to 16-31°C; the device itself accepts 0-50.

## Fan speed

ECHONET Lite defines eight fan levels (`0x31`-`0x38` in EPC `0xA0`) plus auto,
but units implement fewer, and **a set above what the unit supports is not
rejected** - it returns `Set_Res` like any other write and is then silently
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
`SO_REUSEADDR` will steal each other's datagrams - only one socket receives
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
| 0xB3 | Setpoint °C                | `0x00`-`0x32`, else undefined |
| 0xBB | Measured room temp °C      | signed byte |
| 0xBE | Measured outdoor temp °C   | signed byte |
| 0xA0 | Fan speed                  | `0x41` auto, `0x31`-`0x38` levels 1-8 |
| 0x88 | Fault status               | `0x41` fault, `0x42` no fault |
| 0x85 | Cumulative energy          | 4 bytes, units of 0.001 kWh |

`dump` prints these plus everything else the unit says it can return, which
is the quickest way to see what a different model exposes. Note that not
every aircon reports outdoor temperature (`0xBE`) or energy (`0x85`); the
module omits missing fields from the tooltip rather than showing zeroes.

## Security

ECHONET Lite as specified has no authentication whatsoever - anything on the
same L2 network can read and control the unit. That is what makes this module
possible, and it is also worth knowing about: keep the device off any network
you don't trust, and don't expose UDP 3610 to the internet.

## Licence

MIT. See [LICENSE](LICENSE).
