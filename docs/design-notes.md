# Design notes

Why this is built the way it is. None of it is needed to use the module - see
the [README](../README.md) for that. It is here because most of it cost real
debugging time and none of it is obvious from the ECHONET Lite spec.

## Protocol

### Replies come back to port 3610, not to the source port

A conventional "send from an ephemeral port, read the answer" client just
times out. The socket has to bind 3610 itself. Handled in `echonetlite.py`.

### Which makes concurrent polling unsafe

Two processes bound to 3610 with `SO_REUSEADDR` steal each other's datagrams -
only one socket receives any given packet. So reads go through an flock plus a
short-lived cache (`$XDG_RUNTIME_DIR/waybar-heatpump.json`, 20s, override with
`$HEATPUMP_CACHE_TTL`), and every response is matched on its transaction ID
before being accepted.

### A fan speed above what the unit supports is not rejected

It returns `Set_Res` like any successful write and is then silently ignored.
The ceiling has to be measured, which is what `calibrate` does: it walks down
from level 8 writing and reading back each one until it finds the highest that
sticks, restores the fan to where it was, and records `fan_max`. The unit this
was written against tops out at 6 of a possible 8.

### Property maps have two encodings

Under 16 properties they are a plain list. At 16 or more they switch to a
transposed bitmap where bit `row` of byte `col` means property
`((row+8)<<4)|col`. `decode_property_map` handles both.

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

Discovery asks every node for property `0xD6` (self-node instance list) and
picks the first advertising an EOJ in class `0130`.

### What this unit does not report

Probing the rest of the aircon class turned up no humidity (`0xBA`), no coil
or discharge temperatures, and no instantaneous power (`0x84`). Lifetime
energy (`0x85`) is read but not displayed: it is a cumulative counter that
moves in 0.1 kWh steps, so it says nothing about what the unit is doing right
now. It still shows up in `dump`, which is useful for telling whether the unit
is drawing power at all.

## The panel

### Locating the module

waybar exposes no geometry, so `panel-position.py` measures the bar itself: it
grabs the top strip of the screen, marks every column carrying a glyph, and
takes the content starting after the widest gap in the right half - that gap
is the space between the centred clock and the right-hand group.

That finds where the module *starts*. Finding where it ends by looking for the
next gap does not work: the space between `hp off` and the next module can be
~12px, narrower than the spacing between the words inside `hp off` itself, so
any threshold either splits the module in half or swallows its neighbour. The
width comes from the text instead - waybar renders DejaVu Sans Mono at 9px and
the font is exactly 0.6 em per character, so the module is its character count
times 5.41px wide.

Two approaches that do not work:

- **Sampling the bar's background colour.** waybar is translucent, so its
  "background" is the wallpaper underneath and differs at every x. A luminance
  threshold picks out text regardless of what is behind it.
- **Matching the module's own colour.** It changes with the mode, and the
  greys it uses when off are shared with other modules.

The glyph threshold matters more than it looks. waybar is translucent, so the
worst case background is a white wallpaper at 15%: `0.15*255 + 0.85*36 = 69`.
The dimmest text on the bar is `#666666` at luminance 102 - exactly what this
module goes when the unit is **off**. A threshold of 130 loses the module in
that state and silently reports the next one along, so the panel opens under
the wrong thing precisely when the unit is off. It sits at 85.

### Keeping up with the module, without being slow to open

The module is leftmost in `modules-right`, so it slides whenever anything to
its right changes width - the wifi percentage alone moves it several pixels.
`heatpump-track.py` re-measures every 1.5s while the panel is open and moves
it, then exits once the panel is gone.

It runs as its own process for a reason. Repositioning means `eww open` on an
already-open window, which re-renders it and restarts any `deflisten` that
window owns. A tracker living inside the panel's listener therefore kills
itself every time it moves the window and comes back up with no memory of
having done so, which loops forever.

The measurement does not happen before the window opens. A grim capture plus a
Pillow import plus a couple of interpreter starts is ~180ms, and doing it up
front made it most of the gap between the click and the panel appearing. The
position is almost always what it was last time, so the panel opens at the
remembered one immediately and the tracker corrects it - taking its first
measurement straight away rather than after its interval, and writing each
result to `$XDG_RUNTIME_DIR/waybar-heatpump.xpos`. Only the first open after a
reboot measures. Open time went from ~0.36s to ~0.10s.

When the remembered position is wrong it is wrong by a few pixels, because the
module's text changed width (`hp off` -> `hp 21°C`), and the correction lands
about 200ms after the panel appears.

### Keyboard focus, and why there are two windows

eww 0.5 exposes keyboard focus as a single boolean, which maps to layer-shell
`exclusive`. There is no on-demand mode. An exclusive layer surface takes
**every keystroke on the system** for as long as it is open, so a focusable
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

### Dismissal

Neither click-off nor Escape is something eww can do by itself:

- A layer-shell surface cannot see clicks that land outside it, so
  `heatpump-backdrop` is a full-screen transparent window one layer below the
  panel whose only job is to catch them. It must paint something - a fully
  transparent box takes no pointer events - so it paints black at 1% alpha.
- eww 0.5 has no key events at all. Escape is bound in sway with
  `swaymsg bindsym` when the panel opens and removed with `unbindsym` when it
  closes, so Escape reaches applications normally the rest of the time.

### Live updates

The listener runs only while the panel is open, so nothing polls the device
the rest of the time. It reads through the same cache and flock as the waybar
module rather than talking to the unit itself, for the reason in the protocol
section above. Control actions patch the cache with the value they sent and
touch a poke file the listener watches, so a button press redraws immediately
instead of waiting for the unit to catch up.

### Appearance

The panel is deliberately not a floating card: waybar's exact background
(`rgba(36,36,36,0.85)`, translucency included), the same font and palette,
square corners, flush against the bar's bottom edge, centred on the module.

An accent stripe across the top was tried and dropped. Sharing the bar's exact
background and sitting flush against it already reads as attached, and a
coloured stub on top of that just looked stuck on.

Two GTK details worth knowing if you restyle it:

- The big temperature is centred on its **digits**, not on digits-plus-degree.
  The box carries a left margin of one degree-glyph and GTK splits an
  asymmetric margin when centring, so the group shifts right by half a glyph,
  cancelling the degree sign on the other side.
- The hairline separators are childless boxes. Give one a child, even an empty
  label, and GTK grows it to a full line height so it reads as an empty input
  field.

## Rejected: scroll to change temperature

`warmer`/`cooler` on `on-scroll-up`/`on-scroll-down` was the first thing
tried. A scroll gesture that happens to cross the module walks the setpoint to
the end of its range, which in heat mode looks exactly like the unit having
switched itself off. The commands still exist for the CLI; they are just not
worth binding to a wheel.
