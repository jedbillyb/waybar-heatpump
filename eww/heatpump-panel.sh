#!/bin/bash
# Open/close the heat pump panel, and swap it between its read-only and
# editable windows.
#
#   heatpump-panel.sh              toggle the panel
#   heatpump-panel.sh --edit X     swap to the focusable editing window
#   heatpump-panel.sh --show X     swap back to the read-only panel
#
# There are two windows because eww 0.5 only supports exclusive keyboard
# focus: a focusable window takes every keystroke on the system for as long as
# it is open, so the panel you leave open cannot be the focusable one.
#
# The panel's x position is measured at open time rather than hardcoded. The
# heat pump module is the leftmost entry in modules-right, so it slides
# sideways whenever anything to its right changes width - the wifi percentage
# alone moves it several pixels.

set -eu

EWW_DIR="$HOME/.config/eww"
PADDING=14      # the panel's own padding, so its text lines up with the
                # module's text rather than with the module's left edge

eww ping >/dev/null 2>&1 || eww daemon >/dev/null 2>&1 || true

case "${1:-}" in
    --edit)
        eww close heatpump 2>/dev/null || true
        exec eww open heatpump-edit --arg "xpos=${2:-1232}"
        ;;
    --show)
        eww close heatpump-edit 2>/dev/null || true
        exec eww open heatpump --arg "xpos=${2:-1232}"
        ;;
esac

if eww active-windows 2>/dev/null | grep -q '^heatpump'; then
    eww close heatpump 2>/dev/null || true
    eww close heatpump-edit 2>/dev/null || true
    exit 0
fi

x=$(python3 "$EWW_DIR/panel-position.py" 2>/dev/null || echo 1246)
x=$(( x > PADDING ? x - PADDING : 0 ))
exec eww open heatpump --arg "xpos=$x"
