#!/bin/bash
# Open/close the heat pump panel. Bound to a left click on the waybar module.
#
# `eww open --toggle` is enough on its own, but the daemon has to be running
# first or the first click after a fresh login does nothing.

set -eu

eww ping >/dev/null 2>&1 || eww daemon >/dev/null 2>&1 || true
exec eww open --toggle heatpump
