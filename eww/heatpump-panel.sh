#!/bin/bash
# Open/close the heat pump panel, and swap it between its read-only and
# editable windows.
#
#   heatpump-panel.sh              toggle the panel
#   heatpump-panel.sh --close      close everything
#   heatpump-panel.sh --edit X     swap to the focusable editing window
#   heatpump-panel.sh --show X     swap back to the read-only panel
#
# There are two panel windows because eww 0.5 only supports exclusive keyboard
# focus: a focusable window takes every keystroke on the system for as long as
# it is open, so the panel you leave open cannot be the focusable one.
#
# Dismissal needs help from outside eww. A layer-shell surface cannot see
# clicks that land elsewhere, hence the transparent backdrop window, and eww
# 0.5 has no key events at all, hence binding Escape in sway for exactly as
# long as the panel is up.

set -eu

EWW_DIR="$HOME/.config/eww"
SELF="$EWW_DIR/heatpump-panel.sh"
WIDTH=268       # the panel window's width; the panel is centred inside it, so
                # centring the window on the module centres the panel too

eww ping >/dev/null 2>&1 || eww daemon >/dev/null 2>&1 || true

TRACKER_PID="${XDG_RUNTIME_DIR:-/tmp}/waybar-heatpump.trackpid"

close_all() {
    [ -f "$TRACKER_PID" ] && kill "$(cat "$TRACKER_PID")" 2>/dev/null || true
    rm -f "$TRACKER_PID"
    eww close heatpump 2>/dev/null || true
    eww close heatpump-edit 2>/dev/null || true
    eww close heatpump-backdrop 2>/dev/null || true
    swaymsg 'unbindsym Escape' >/dev/null 2>&1 || true
}

case "${1:-}" in
    --close)
        close_all
        exit 0
        ;;
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
    close_all
    exit 0
fi

# The module's width comes from its own text, so pass it through.
text=$(python3 "$HOME/.config/waybar/heatpump-status.py" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["text"])' \
        2>/dev/null || echo "hp")
centre=$(python3 "$EWW_DIR/panel-position.py" "$text" 2>/dev/null || echo 1280)
x=$(( centre - WIDTH / 2 ))
[ "$x" -lt 0 ] && x=0

eww open heatpump-backdrop
eww open heatpump --arg "xpos=$x"

# Follows the module as the bar reflows. Its own process, because moving the
# window restarts anything the window itself spawned.
setsid python3 "$EWW_DIR/heatpump-track.py" "$x" >/dev/null 2>&1 &
echo $! > "$TRACKER_PID"

# Only bound while the panel is up, so Escape reaches applications normally
# the rest of the time.
swaymsg "bindsym Escape exec $SELF --close" >/dev/null 2>&1 || true
