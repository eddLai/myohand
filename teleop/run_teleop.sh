#!/usr/bin/env bash
# RH56F1 hand-gesture teleop launcher
# Opens the MediaPipe finger-tracking window with a SYNC button that
# mirrors your gestures onto the Inspire RH56F1.
#
#   ./run_teleop.sh                       # stream into handd (default)
#   ./run_teleop.sh --iface=enp17s0       # ...starting handd itself
#   ./run_teleop.sh --sink=none           # no hand, no daemon: vision only
#   ./run_teleop.sh --device 2 --rate 30  # anything teleop_app.py accepts
#
# With --iface (or $ECAT_IFACE) this starts handd, waits for its socket,
# runs teleop, and stops the daemon again when teleop exits - including on
# Ctrl+C. One terminal, one interrupt, nothing left holding the bus.
#
# It only stops a daemon it started. If one is already answering, this
# uses it and leaves it running, because killing something another window
# is driving would be a worse surprise than leaving it up.
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

# --iface=NAME is ours, not teleop_app.py's; everything else passes through
IFACE="${ECAT_IFACE:-}"
ARGS=()
for a in "$@"; do
    case "$a" in
        --iface=*) IFACE="${a#--iface=}" ;;
        *) ARGS+=("$a") ;;
    esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

SOCK="${HAND_SOCKET:-/tmp/inspire_hand.sock}"
DAEMON_PID=""
TELEOP_PID=""

daemon_answers() {
    python3 - "$SOCK" <<'EOF' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1.0)
s.connect(sys.argv[1])
EOF
}

# Stop a child and mean it. TERM first, KILL if it will not go: the first
# version only stopped the daemon and left teleop running, still holding
# /dev/video0, so the next run blocked on the camera instead of starting.
# An interrupted OpenCV read does not always come back to Python's signal
# handler, so "it should have exited" is not enough here.
stop_child() {
    local pid=$1 what=$2
    [ -n "$pid" ] || return 0
    kill -0 "$pid" 2>/dev/null || return 0
    echo "run_teleop: stopping $what (pid $pid)" >&2
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 50); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.1
    done
    echo "run_teleop: $what did not stop on TERM, killing" >&2
    kill -KILL "$pid" 2>/dev/null || true
}

cleanup() {
    # teleop first: it is the one holding the camera, and the daemon is
    # happier being left last anyway - it parks the hand on the way out.
    stop_child "$TELEOP_PID" "teleop"
    stop_child "$DAEMON_PID" "the handd it started"
}
# INT/TERM exit, which runs the EXIT trap once - keeping the teardown in a
# single place rather than three that can disagree.
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT

pick_python() {
    if [ -n "${TELEOP_PYTHON:-}" ]; then echo "$TELEOP_PYTHON"; return; fi
    for p in "../venv/bin/python3" "$HOME/myohand/venv/bin/python3"; do
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

if daemon_answers; then
    echo "run_teleop: using the handd already on $SOCK (leaving it running)" >&2
elif [ -n "$IFACE" ]; then
    [ -x ../hand_fw/handd ] || { echo "../hand_fw/handd is not built - run: make -C ../hand_fw handd && sudo make -C ../hand_fw cap" >&2; exit 1; }
    echo "run_teleop: starting handd on $IFACE" >&2
    ../hand_fw/handd --iface="$IFACE" --socket="$SOCK" &
    DAEMON_PID=$!
    for _ in $(seq 1 100); do
        daemon_answers && break
        kill -0 "$DAEMON_PID" 2>/dev/null || { echo "handd exited during start-up" >&2; exit 1; }
        sleep 0.1
    done
    daemon_answers || { echo "handd never opened $SOCK" >&2; exit 1; }
fi

# A bare first argument stays the camera index, the way this script has
# always been called; anything starting with - goes to teleop_app.py.
# Not exec: the EXIT trap has to survive to stop the daemon.
# Backgrounded and waited on rather than run in the foreground, so an
# interrupt reaches the trap with the child still reapable instead of
# orphaning it.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
    DEV=$1
    shift
    "$PY" teleop_app.py --device "$DEV" --socket "$SOCK" "$@" &
else
    "$PY" teleop_app.py --socket "$SOCK" "$@" &
fi
TELEOP_PID=$!
wait "$TELEOP_PID"
