"""The RH56F1 target scale - the one Python copy of it.

The C side defines this scale in hand_safety.h and nothing else in the C
tree restates it. Python cannot call into that header, so this module is
its mirror, and `verify()` checks the mirror against `hand_ctl scale`
rather than trusting that a future edit touched both. Any Python code
that converts between ANGLEACT and a command target imports from here.

UNRESOLVED (2026-08-06): the command field may not be a 0..2000 scale at
all but an ANGLEACT-style setpoint (~890..1850) - see hand_safety.h for
the two observations behind that doubt. Settling it needs the hand. When
it is settled, the numbers below change in one place and every caller
follows; `verify()` is what will catch a half-finished change.
"""
import json
import os
import subprocess

TARGET_MIN = 0
TARGET_MAX = 2000
TARGET_HOLD = -1          # leave the axis wherever it currently is

ANG_CLOSED = 890          # ANGLEACT span measured on this unit
ANG_OPEN = 1850

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
    """Where a joint actually sits, on the same scale the targets use."""
    span = (int(ang) - ANG_CLOSED) * (TARGET_MAX - TARGET_MIN) / (ANG_OPEN - ANG_CLOSED)
    return max(TARGET_MIN, min(TARGET_MAX, int(TARGET_MIN + span)))


def target_to_ang(tgt):
    """Inverse of ang_to_target. A hold names no angle, so it stays a hold
    instead of being invented into a move."""
    if int(tgt) == TARGET_HOLD:
        return TARGET_HOLD
    span = (clamp_target(tgt) - TARGET_MIN) * (ANG_OPEN - ANG_CLOSED) / (TARGET_MAX - TARGET_MIN)
    return max(ANG_CLOSED, min(ANG_OPEN, int(ANG_CLOSED + span)))


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
