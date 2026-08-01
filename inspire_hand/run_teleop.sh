#!/usr/bin/env bash
# RH56F1 hand-gesture teleop launcher
# Opens the MediaPipe finger-tracking window (overhead cam) with a SYNC
# button that mirrors your gestures onto the Inspire RH56F1 robot hand.
export DISPLAY="${DISPLAY:-:0}"
if [ -z "$XAUTHORITY" ]; then
    for f in "$HOME/.Xauthority" /run/user/$(id -u)/.mutter-Xwaylandauth.*; do
        [ -e "$f" ] && export XAUTHORITY="$f" && break
    done
fi
cd "$(dirname "$0")"
exec "$HOME/inspire_hand/venv/bin/python3" teleop_app.py --device "${1:-4}"
