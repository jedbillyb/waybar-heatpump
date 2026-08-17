#!/usr/bin/env python3
"""Feeds the eww heat pump panel one JSON line per update.

Runs only while the panel is open (eww starts a `deflisten` when the window
opens and kills it when it closes), so the polling here costs nothing the
rest of the time.

It reads through heatpump-status.py's cache rather than hitting the device
itself: the reply to an ECHONET Lite Get lands on port 3610, so two pollers
running at once would steal each other's datagrams. The cache plus its flock
is what keeps this and the waybar module from colliding.

A control action patches the cache with the value it just sent and touches
the poke file, so the panel reflects a button press immediately instead of
waiting for the device to catch up.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "heatpump_status",
    os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                 "heatpump-status.py"))
hp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hp)

POKE = os.path.join(hp.RUNTIME, "waybar-heatpump.poke")
POLL = 1.0          # how often we look for a change
REFRESH = 5.0       # how stale the device reading may get while open


def main():
    last_poke = 0.0
    last_emit = 0.0
    last_payload = None

    while True:
        try:
            poke = os.path.getmtime(POKE)
        except OSError:
            poke = 0.0

        # A button press forces an immediate redraw; otherwise refresh on a
        # slow timer so an adjustment made at the wall unit still shows up.
        forced = poke != last_poke
        if forced or time.time() - last_emit >= REFRESH:
            last_poke = poke
            last_emit = time.time()
            payload = json.dumps(hp.state_json(max_age=0 if forced else REFRESH))
            if payload != last_payload:
                last_payload = payload
                print(payload, flush=True)

        time.sleep(POLL)


if __name__ == "__main__":
    main()
