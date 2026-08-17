#!/usr/bin/env python3
"""Keep the panel sitting under the bar module while it is open.

The heat pump module is the leftmost entry in `modules-right`, so it slides
sideways whenever anything to its right changes width - the wifi percentage
and the AirPods module do it on their own. Measuring once at open time leaves
the panel stranded, so re-measure and move it.

This runs as its own process rather than inside the panel's listener.
Repositioning means `eww open` on an already-open window, which re-renders it
and restarts any `deflisten` it owns - so a tracker living inside the listener
kills itself every time it moves the window, and comes back up with no memory
of having moved it. That loops forever.

Started by heatpump-panel.sh when the panel opens; exits on its own once the
panel is gone, so nothing is left running.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
INTERVAL = 1.5
PADDING = 14        # the panel's padding, so its text lines up with the
                    # module's text rather than the module's left edge
JITTER = 2          # ignore sub-pixel wobble in the measurement


def measure():
    out = subprocess.run(["python3", os.path.join(HERE, "panel-position.py")],
                         capture_output=True, text=True, timeout=5).stdout
    return max(0, int(out.strip()) - PADDING)


def panel_open():
    windows = subprocess.run(["eww", "active-windows"],
                             capture_output=True, text=True).stdout
    return "heatpump:" in windows or "heatpump-edit:" in windows


def main():
    applied = int(sys.argv[1]) if len(sys.argv) > 1 else None

    while True:
        time.sleep(INTERVAL)
        if not panel_open():
            return

        try:
            x = measure()
        except (ValueError, OSError, subprocess.SubprocessError):
            continue

        if applied is not None and abs(x - applied) <= JITTER:
            continue

        # Only move the read-only panel. Reopening the editing window would
        # reset the entry under whoever is typing in it.
        windows = subprocess.run(["eww", "active-windows"],
                                 capture_output=True, text=True).stdout
        if "heatpump:" not in windows:
            continue

        subprocess.run(["eww", "open", "heatpump", "--arg", "xpos=%d" % x],
                       capture_output=True)
        applied = x


if __name__ == "__main__":
    main()
