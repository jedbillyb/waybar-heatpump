#!/usr/bin/env python3
"""Work out where the heat pump module sits on the bar, so the panel drops
straight out of it instead of floating in a corner.

waybar exposes no geometry, so this measures the bar itself: grab the top
strip of the screen, mark every column that has a glyph on it, and look at
the gaps. The heat pump module is the first entry in `modules-right`, and the
space between the centred clock and the right-hand group is far wider than
the spacing between neighbouring modules - so the content starting after the
widest gap in the right half of the bar is the module.

Two approaches that don't work, for the next person:

- Comparing against a background colour sampled from the bar. waybar is
  translucent, so its "background" is the wallpaper underneath and differs at
  every x. A luminance threshold picks out text regardless.
- Matching the module's own colour. It changes with the mode, and the greys
  it uses when off are shared with other modules.

Prints the left edge of the module, or its centre if given the module's text.

Finding the far edge by looking for a gap does not work. The space between
"hp off" and the module after it can be ~12px, which is narrower than the
spacing between the words inside "hp off" itself - so any threshold either
splits the module in half or swallows its neighbour. Measuring gaps to find
where the module *starts* is fine; measuring them to find where it ends is
not.

The width comes from the text instead: waybar renders DejaVu Sans Mono at
9px, and the font is exactly 0.6 em per character, so a module's width is its
character count times 5.41px.
"""

import io
import json
import subprocess
import sys

BAR_HEIGHT = 16
# Glyph luminance has to clear the bar's background but still catch the
# module's dim states. waybar is translucent, so the worst-case background is
# a white wallpaper at 15%: 0.15*255 + 0.85*36 = 69. The dimmest text on the
# bar is #666666 at 102 - which is exactly what the heat pump module goes when
# the unit is off. A threshold of 130 loses the module in that state and
# silently reports the next one along.
TEXT_LUMA = 85
MIN_GAP = 20
CHAR_W = 5.41       # DejaVu Sans Mono at 9px: 0.6 em per character
FALLBACK_FROM_RIGHT = 674


def screen_width():
    try:
        out = subprocess.run(["swaymsg", "-t", "get_outputs", "-r"],
                             capture_output=True, text=True, check=True).stdout
        outputs = json.loads(out)
        for o in outputs:
            if o.get("focused"):
                return o["rect"]["width"]
        return outputs[0]["rect"]["width"]
    except Exception:
        return 1920


def content_columns(width):
    png = subprocess.run(
        ["grim", "-g", "0,0 %dx%d" % (width, BAR_HEIGHT), "-"],
        capture_output=True, check=True).stdout
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    px = im.load()
    out = []
    for x in range(w):
        lit = False
        for y in range(h):
            r, g, b = px[x, y]
            if 0.299 * r + 0.587 * g + 0.114 * b > TEXT_LUMA:
                lit = True
                break
        out.append(lit)
    return out


def main():
    width = screen_width()
    try:
        cols = content_columns(width)
    except Exception:
        print(max(0, width - FALLBACK_FROM_RIGHT))
        return

    # Collapse glyph columns into gaps, then pick the widest gap that starts
    # in the right half of the bar. Anything narrower is inter-module spacing.
    best_gap, best_end = 0, None
    x = 0
    while x < width:
        if cols[x]:
            x += 1
            continue
        start = x
        while x < width and not cols[x]:
            x += 1
        # A trailing gap runs to the screen edge and marks nothing.
        if x >= width:
            break
        gap = x - start
        if gap >= MIN_GAP and start > width * 0.45 and gap > best_gap:
            best_gap, best_end = gap, x

    left = best_end if best_end is not None else max(
        0, width - FALLBACK_FROM_RIGHT)

    text = sys.argv[1] if len(sys.argv) > 1 else ""
    print(int(left + len(text) * CHAR_W / 2) if text else left)


if __name__ == "__main__":
    main()
