# waybar-heatpump

Control your heat pump from your status bar. Works with any air conditioner
that speaks **ECHONET Lite** on your local network - no cloud account, no
vendor app.

```
hp 22°C
```

Left click opens a control panel that hangs off the module. Right click
toggles power.

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

## Features

- **Bar module** showing mode, setpoint and room temperature, with a tooltip
  carrying the full state. Colours itself by what the unit is doing.
- **Control panel** for power, mode, setpoint and fan speed. Set the
  temperature with the steppers or click the number and type it.
- **Live readings** for target, room and outdoor temperature.
- **Fault indicator** when the unit reports a problem.
- No polling at all while the panel is closed.

Written against a Mitsubishi Electric unit, but it uses only standard ECHONET
Lite properties and reads the device's own property map, so it should work
with any ECHONET Lite aircon.

## Requirements

The bar module needs **nothing beyond Python 3**. If you only want the module
and its tooltip, that is the whole list.

The panel additionally needs:

| Package | For |
|---------|-----|
| [eww](https://github.com/elkowar/eww) 0.5+ | the panel |
| `grim` | locating the module on the bar |
| Pillow | reading that screenshot |
| sway | closing the panel on Escape |

Only Escape is sway-specific. On another wlroots compositor everything else
works; see [Other compositors](#other-compositors).

## Install

```sh
git clone https://github.com/jedbillyb/waybar-heatpump
cd waybar-heatpump

cp heatpump-status.py echonetlite.py ~/.config/waybar/
cp eww/* ~/.config/eww/
```

Tell eww about the panel:

```sh
echo '(include "heatpump.yuck")' >> ~/.config/eww/eww.yuck
echo '@import "heatpump";'       >> ~/.config/eww/eww.scss
```

Add the module to `~/.config/waybar/config`:

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

Then add `"custom/heatpump"` to your `modules-right` list.

Style it in `~/.config/waybar/style.css`:

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

> Keep the `padding`. The panel finds the module by reading the bar as pixels,
> and a module jammed against its neighbour is harder to locate.

Finally, run this once:

```sh
~/.config/waybar/heatpump-status.py calibrate
```

Most units support fewer than the eight fan speeds ECHONET Lite defines, and
they accept a too-high setting without complaining and then ignore it.
`calibrate` finds the real ceiling by testing each speed, and saves it. Skip
this and the top fan steps will silently do nothing.

Reload waybar (`pkill -SIGUSR2 waybar`) and you should see the module.

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
heatpump-status.py settemp N  set the target temperature
heatpump-status.py calibrate  find the unit's real top fan speed
heatpump-status.py dump       every readable property, decoded
heatpump-status.py discover   find ECHONET Lite nodes on the LAN
```

Bind any of these to a key, or call them from scripts.

Setpoints are clamped to 16-31°C. Scroll bindings are deliberately not
suggested: a stray scroll across the module walks the setpoint to the end of
its range.

## Configuration

The heat pump is found automatically by multicast discovery, so there is
usually nothing to configure. To pin it, create
`~/.config/waybar/heatpump.conf`:

```
ip = 192.168.68.50
fan_max = 6
```

`fan_max` is written by `calibrate`. A bare line with no `key =` is read as
the IP, so the minimal file is just an address.

Environment variables override the file: `$HEATPUMP_IP`,
`$HEATPUMP_FAN_MAX`, `$HEATPUMP_CACHE_TTL`, `$HEATPUMP_HIDE_AFTER`.

## Away from the heat pump

The module takes no space in the bar when there is nothing to report. If the
device address does not resolve to an attached link - you are on another
network, or tethered - the module emits empty text and waybar drops it, with
no packets sent and no discovery timeout to wait through. If you are on the
right network but the unit does not answer, it shows `hp --` for one poll and
then hides, so a single lost datagram does not make it flicker in and out.
`$HEATPUMP_HIDE_AFTER` sets how many consecutive failed polls that takes
(default 2); set it to 1 to hide on the first miss.

It comes straight back on the next successful poll.

Run `heatpump-status.py discover` to see what is on your network.

## Troubleshooting

**Module is missing from the bar** - that is deliberate when the heat pump is
off-network or not answering; see "Away from the heat pump" above. If it is
missing while the unit is up, run `heatpump-status.py` by hand to see what it
emits.

**Module says `hp --` or "unreachable"** - the unit was not found. Run
`discover` to check it is visible, and pin the IP in the config file if
discovery is unreliable on your network. Discovery is multicast, so it needs
the heat pump on the same L2 network as your machine - it will not cross a
VLAN or a guest network.

**Fan speed will not go above a certain level** - that is your unit's real
ceiling. Run `calibrate` to record it.

**Panel opens under the wrong module** - it locates the module by reading the
bar's pixels, which assumes DejaVu Sans Mono at 9px and the module first in
`modules-right`. If your bar differs, set `FALLBACK_FROM_RIGHT` in
`eww/panel-position.py`, or adjust `CHAR_W` for your font.

**Panel is slightly off the first time, then jumps into place** - expected. It
opens at the last known position for speed and corrects within ~200ms.

**Cannot type anywhere while the panel is open** - the panel should never hold
the keyboard except while you are typing a temperature. If it is stuck, close
it with `~/.config/eww/heatpump-panel.sh --close`.

**No outdoor temperature** - not every unit reports it. Missing values are
omitted rather than shown as zero. `dump` shows what yours supports.

### Other compositors

Everything works on any wlroots compositor except the Escape-to-close binding,
which uses `swaymsg`. Replace the two `swaymsg` lines in
`eww/heatpump-panel.sh` with your compositor's equivalent, or delete them and
close the panel by clicking outside it.

## Security

ECHONET Lite has **no authentication of any kind**. Anything on the same
network can read and control your heat pump. That is what makes this project
possible, and it is worth knowing about: keep the device off networks you do
not trust, and never expose UDP port 3610 to the internet.

## How it works

For the protocol details, the ECHONET Lite quirks that are not in the spec,
and why the panel is built the way it is, see
[docs/design-notes.md](docs/design-notes.md).

## Licence

MIT. See [LICENSE](LICENSE).
