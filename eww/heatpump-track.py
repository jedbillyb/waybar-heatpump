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

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
XPOS = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                    "waybar-heatpump.xpos")
INTERVAL = 1.5
WIDTH = 268         # the panel window's width; the panel is centred inside it,
                    # so centring the window centres the panel on the module
JITTER = 2          # ignore sub-pixel wobble in the measurement


def module_text():
    """The module's own bar text, which is what sets its width."""
    status = os.path.join(os.path.dirname(HERE), "heatpump-status.py")
    try:
        out = subprocess.run(["python3", status], capture_output=True,
                             text=True, timeout=5).stdout
        return json.loads(out).get("text", "")
    except (ValueError, OSError, subprocess.SubprocessError):
        return ""


def measure():
    out = subprocess.run(
        ["python3", os.path.join(HERE, "panel-position.py"), module_text()],
        capture_output=True, text=True, timeout=8).stdout
    return max(0, int(out.strip()) - WIDTH // 2)


def panel_open():
    windows = subprocess.run(["eww", "active-windows"],
                             capture_output=True, text=True).stdout
    return "heatpump:" in windows or "heatpump-edit:" in windows


def remember(x):
    """Record the position so the next open can skip the measurement."""
    try:
        with open(XPOS, "w") as fh:
            fh.write("%d\n" % x)
    except OSError:
        pass


def main():
    applied = int(sys.argv[1]) if len(sys.argv) > 1 else None

    # First pass runs immediately, not after INTERVAL: the panel has just been
    # opened at a remembered position that may be a few pixels stale, and that
    # is exactly the moment someone is looking at it.
    first = True

    while True:
        if not first:
            time.sleep(INTERVAL)
        first = False

        if not panel_open():
            return

        try:
            x = measure()
        except (ValueError, OSError, subprocess.SubprocessError):
            continue

        remember(x)

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
