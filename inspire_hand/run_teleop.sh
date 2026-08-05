#!/usr/bin/env bash
# RH56F1 hand-gesture teleop launcher
# Opens the MediaPipe finger-tracking window with a SYNC button that
# mirrors your gestures onto the Inspire RH56F1.
#
#   ./run_teleop.sh                       # stream into handd (default)
#   ./run_teleop.sh --sink=none           # no hand, no daemon: vision only
#   ./run_teleop.sh --device 2 --rate 30  # anything teleop_app.py accepts
#
# The interpreter is found rather than hardcoded, in this order:
#   $TELEOP_PYTHON  ->  ./venv  ->  $HOME/inspire_hand/venv  ->  python3
# because this now runs on at least three machines whose venvs are in
# three different places, and a wrong absolute path here used to be the
# first thing anyone hit.
set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
    for f in "$HOME/.Xauthority" /run/user/"$(id -u)"/.mutter-Xwaylandauth.*; do
        [ -e "$f" ] && export XAUTHORITY="$f" && break
    done
fi

cd "$(dirname "$0")"

pick_python() {
    if [ -n "${TELEOP_PYTHON:-}" ]; then echo "$TELEOP_PYTHON"; return; fi
    for p in "./venv/bin/python3" "$HOME/inspire_hand/venv/bin/python3"; do
        [ -x "$p" ] && { echo "$p"; return; }
    done
    command -v python3
}

PY=$(pick_python)
if ! "$PY" -c 'import cv2, mediapipe' 2>/dev/null; then
    echo "$PY cannot import cv2 and mediapipe." >&2
    echo "Point TELEOP_PYTHON at an interpreter that can:" >&2
    echo "  TELEOP_PYTHON=/path/to/venv/bin/python3 $0 $*" >&2
    echo "On the KD240, do NOT run setup.sh to fix this - see its header." >&2
    exit 1
fi

# A bare first argument stays the camera index, the way this script has
# always been called; anything starting with - goes to teleop_app.py.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
    DEV=$1
    shift
    exec "$PY" teleop_app.py --device "$DEV" "$@"
fi
exec "$PY" teleop_app.py "$@"
