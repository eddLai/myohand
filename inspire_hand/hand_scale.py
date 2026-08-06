"""The RH56F1 target scale - the one Python copy of it.

The C side defines this scale in hand_safety.h and nothing else in the C
tree restates it. Python cannot call into that header, so this module is
its mirror, and `verify()` checks the mirror against `hand_ctl scale`
rather than trusting that a future edit touched both. Any Python code
that converts between ANGLEACT and a command target imports from here.

SETTLED on the hand (2026-08-06): a command is an ANGLEACT setpoint, one
count for one count - commanded 1100 read back 1101, 1272 read back 1274,
1509 read back 1508, and 612 (below the closed end) drove the axis into
the closed stop at 896. It was never a 0..2000 scale. The conversions
below are therefore the identity, kept as functions so callers that speak
through them keep working and so a unit with different travel has one
place to change. See hand_safety.h for the same note on the C side.
"""
import json
import os
import subprocess

TARGET_MIN = 890          # fully closed - the mechanism's own stop
TARGET_MAX = 1850         # fully open
TARGET_HOLD = -1          # leave the axis wherever it currently is

ANG_CLOSED = 890          # ANGLEACT span measured on this unit - the same
ANG_OPEN = 1850           # scale as the targets, not a second one

HAND_CTL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_ctl")


def clamp_target(tgt):
    """Clamp into range. TARGET_HOLD passes through as itself."""
    tgt = int(tgt)
    if tgt == TARGET_HOLD:
        return TARGET_HOLD
    return max(TARGET_MIN, min(TARGET_MAX, tgt))


def target_valid(tgt):
    return int(tgt) == TARGET_HOLD or TARGET_MIN <= int(tgt) <= TARGET_MAX


def ang_to_target(ang):
    """Where a joint actually sits, on the same scale the targets use -
    which is the same number, clamped into travel."""
    return max(TARGET_MIN, min(TARGET_MAX, int(ang)))


def target_to_ang(tgt):
    """Inverse of ang_to_target. A hold names no angle, so it stays a hold
    instead of being invented into a move."""
    if int(tgt) == TARGET_HOLD:
        return TARGET_HOLD
    return max(ANG_CLOSED, min(ANG_OPEN, int(tgt)))


def as_dict():
    return {"target_min": TARGET_MIN, "target_max": TARGET_MAX,
            "target_hold": TARGET_HOLD,
            "ang_closed": ANG_CLOSED, "ang_open": ANG_OPEN}


def verify(hand_ctl=HAND_CTL):
    """Compare this module against the C definition.

    Returns None when they agree, a message when they do not, and a
    message starting with "skipped" when the binary is not there to ask
    (a Mac checkout, or a clone that has not been built yet). Reads the
    scale only - `hand_ctl scale` opens no socket and moves nothing.
    """
    if not os.path.exists(hand_ctl):
        return f"skipped: {hand_ctl} not built"
    try:
        r = subprocess.run([hand_ctl, "scale"], capture_output=True,
                           text=True, timeout=10)
        theirs = json.loads(r.stdout.strip().splitlines()[-1])["scale"]
    except Exception as e:                      # noqa: BLE001 - report, do not raise
        return f"skipped: could not read the C scale ({e})"
    mine = as_dict()
    bad = [f"{k}: python {mine[k]} vs C {theirs.get(k)}"
           for k in mine if mine[k] != theirs.get(k)]
    return None if not bad else "scale mismatch - " + "; ".join(bad)
