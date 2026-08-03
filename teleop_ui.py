"""Instrument overlay for the RH56F1 teleop window.

The operator watches their own hand, not the screen, so this panel is
built to be read from the corner of an eye: one large line saying what
to do next, and a schematic hand whose fingers and swinging thumb show
the posture the robot has been commanded to hold.

Palette is the ExoPulse house set - amber for ready, violet while the
hand is moving, rose for anything blocked - on translucent slate so the
video still reads through. Type is Hershey by necessity and by fit: the
plotter faces are the native lettering of instrument panels.
"""
import math

import cv2
import numpy as np

# ExoPulse palette, in BGR
AMBER = (60, 132, 245)      # #F5843C  ready / commanded posture
VIOLET = (217, 84, 140)     # #8C54D9  hand is executing
ROSE = (108, 51, 214)       # #D6336C  blocked or absent
CREAM = (244, 249, 251)     # #FBF9F4  primary text
QUIET = (177, 188, 196)     # secondary text, tinted warm off CREAM, >=5:1 on video
SLATE = (105, 100, 96)      # strokes and inactive tracks only - too dark for text
PANEL = (20, 17, 15)

DISPLAY = cv2.FONT_HERSHEY_TRIPLEX
LABEL = cv2.FONT_HERSHEY_SIMPLEX
METER = cv2.FONT_HERSHEY_PLAIN

# The gauge is drawn as the right hand the robot actually is: thumb on
# the outside, then index through pinky, each finger as long as its real
# counterpart. (label, target index, relative length)
FINGERS = (("IDX", 3, 0.94), ("MID", 2, 1.00), ("RNG", 1, 0.92), ("PKY", 0, 0.72))
BAR_W, BAR_GAP, BAR_MAX = 24, 16, 88
SYNC_BTN = (16, 16, 172, 64)
CAL_BTN = (184, 16, 330, 64)
PARK_BTN = (342, 16, 500, 64)
SET_BTN = (512, 16, 638, 64)

# The settings plate: one row per knob, stepped rather than dragged, because a
# click is the only gesture this window has. (label, key, step, low, high, unit)
ROWS = (("Grip force", "force", 100, 100, 1000, " g"),
        ("Speed", "speed", 100, 50, 1000, ""),
        ("Camera", "device", 1, 0, 5, ""),
        ("Smoothing", "ema", 5, 0, 90, " %"))
PLATE = (16, 80, 452, 78 + len(ROWS) * 46)
ROW0, ROWH = 138, 46
MINUS_X, PLUS_X, STEP_W = 330, 388, 44


def _panel(img, x, y, w, h, border=None, alpha=0.72):
    roi = img[y:y + h, x:x + w]
    if roi.size:
        slab = roi.copy()
        slab[:] = PANEL
        cv2.addWeighted(slab, alpha, roi, 1 - alpha, 0, roi)
    if border:
        cv2.rectangle(img, (x, y), (x + w, y + h), border, 1, cv2.LINE_AA)


def _reached(img, x, base_y, length, frac, color):
    """A tick where the hand actually got to, against the fill it was asked
    for. The two separate whenever a guard clamps or an axis stalls."""
    y = base_y - int(length * frac)
    cv2.line(img, (x - 3, y), (x + BAR_W + 3, y), color, 2, cv2.LINE_AA)


def _finger(img, x, base_y, length, frac, color):
    """A finger stands as tall as it is extended; the unlit remainder is
    a hairline so a closed hand reads as a short finger, not as an empty
    progress bar."""
    lit = int(length * frac)
    cv2.line(img, (x + BAR_W // 2, base_y), (x + BAR_W // 2, base_y - length),
             SLATE, 1, cv2.LINE_AA)
    if lit > 3:
        cv2.rectangle(img, (x, base_y - lit), (x + BAR_W, base_y), color, -1)
        cv2.rectangle(img, (x, base_y - lit), (x + BAR_W, base_y), PANEL, 1, cv2.LINE_AA)


def _thumb_reached(img, pivot, length, frac, angle_deg, color):
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), -math.sin(a)
    px, py = -uy, ux
    d, half = length * frac, BAR_W / 2 + 3
    cv2.line(img,
             (int(pivot[0] + ux * d + px * half), int(pivot[1] + uy * d + py * half)),
             (int(pivot[0] + ux * d - px * half), int(pivot[1] + uy * d - py * half)),
             color, 2, cv2.LINE_AA)


def _thumb(img, pivot, length, frac, angle_deg, color):
    """The thumb swings with the rotation axis, which is what turns this
    gauge from a bar chart into a hand."""
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), -math.sin(a)
    px, py = -uy, ux
    half = BAR_W / 2

    def quad(reach):
        return np.array([(int(pivot[0] + ux * d + px * s * half),
                          int(pivot[1] + uy * d + py * s * half))
                         for d, s in ((0, 1), (reach, 1), (reach, -1), (0, -1))])

    cv2.line(img, pivot, (int(pivot[0] + ux * length), int(pivot[1] + uy * length)),
             SLATE, 1, cv2.LINE_AA)
    reach = length * frac
    if reach > 4:
        cv2.fillConvexPoly(img, quad(reach), color)
        cv2.polylines(img, [quad(reach)], True, PANEL, 1, cv2.LINE_AA)


def draw_gauge(img, tgt, executing, actual=None):
    """Schematic of the posture the robot hand has been commanded to hold."""
    x0, y0, w, h = 606, 80, 340, 200   # shares a baseline with the settings plate
    _panel(img, x0, y0, w, h, border=SLATE, alpha=0.85)
    cv2.putText(img, "COMMANDED POSTURE", (x0 + 14, y0 + 22),
                LABEL, 0.42, QUIET, 1, cv2.LINE_AA)
    if actual:
        cv2.putText(img, "- reached", (x0 + 152, y0 + 22), LABEL, 0.42, CREAM, 1, cv2.LINE_AA)

    base_y = y0 + 140
    span = 4 * BAR_W + 3 * BAR_GAP
    palm_x = x0 + 128
    tone = VIOLET if executing else AMBER
    ghost = tgt is None

    # palm: the piece that makes four bars read as a hand
    cv2.rectangle(img, (palm_x - 8, base_y), (palm_x + span + 8, base_y + 28),
                  SLATE, 1, cv2.LINE_AA)

    x = palm_x
    for label, idx, rel in FINGERS:
        length = int(BAR_MAX * rel)
        frac = 0.0 if ghost else max(0.0, min(1.0, tgt[idx] / 2000))
        _finger(img, x, base_y - 1, length, frac, tone)
        if actual:
            _reached(img, x, base_y - 1, length,
                     max(0.0, min(1.0, actual[idx] / 2000)), CREAM)
        if not ghost:
            cv2.putText(img, label, (x - 1, base_y + 46), METER, 0.8, QUIET, 1, cv2.LINE_AA)
        x += BAR_W + BAR_GAP
    # thumb hinges off the palm's outer edge; tucked at 108deg, splayed at 168deg
    rot = 700 if ghost else max(700, min(2000, tgt[5]))
    swing = 108 + (rot - 700) / 1300 * 60
    _thumb(img, (palm_x + 4, base_y + 18), 76,
           0.0 if ghost else max(0.0, min(1.0, tgt[4] / 2000)), swing, tone)
    if actual:
        _thumb_reached(img, (palm_x + 4, base_y + 18), 76,
                       max(0.0, min(1.0, actual[4] / 2000)), swing, CREAM)
    if ghost:
        cv2.putText(img, "waiting for a hand", (x0 + 14, base_y + 46),
                    LABEL, 0.46, QUIET, 1, cv2.LINE_AA)
    else:
        cv2.putText(img, "THUMB", (x0 + 26, base_y + 46), METER, 0.8, QUIET, 1, cv2.LINE_AA)


def draw_button(img, rect, on, label, tone=AMBER, enabled=True):
    x0, y0, x1, y1 = rect
    edge = tone if on else SLATE
    _panel(img, x0, y0, x1 - x0, y1 - y0, border=edge if enabled else SLATE, alpha=0.85)
    cv2.circle(img, (x0 + 22, (y0 + y1) // 2), 6,
               (tone if on else QUIET) if enabled else SLATE, -1, cv2.LINE_AA)
    cv2.putText(img, label, (x0 + 40, y1 - 18), LABEL, 0.6,
                (CREAM if on else QUIET) if enabled else SLATE, 1, cv2.LINE_AA)


def draw_rail(img, headline, hint, tone, settle_frac, send_secs, telemetry, fps, keys):
    """One line telling the operator what to do, and how far along it is."""
    h_img, w_img = img.shape[:2]
    x0, y0, w, h = 16, h_img - 104, w_img - 32, 88
    _panel(img, x0, y0, w, h, border=SLATE, alpha=0.85)
    cv2.putText(img, headline, (x0 + 20, y0 + 36), DISPLAY, 0.9, tone, 1, cv2.LINE_AA)
    cv2.putText(img, hint, (x0 + 22, y0 + 58), LABEL, 0.44, QUIET, 1, cv2.LINE_AA)
    cv2.line(img, (x0 + 20, y0 + 68), (x0 + w - 20, y0 + 68), (60, 56, 52), 1)
    cv2.putText(img, keys, (x0 + 22, y0 + 82), METER, 0.85, QUIET, 1, cv2.LINE_AA)
    cv2.putText(img, telemetry, (x0 + w - 22 - 7 * len(telemetry), y0 + 82),
                METER, 0.85, QUIET, 1, cv2.LINE_AA)

    tx, ty = x0 + w - 250, y0 + 30
    for i in range(5):
        lit = settle_frac * 5 > i
        cv2.rectangle(img, (tx + i * 16, ty - 10), (tx + i * 16 + 10, ty),
                      tone if lit else SLATE, -1 if lit else 1, cv2.LINE_AA)
    cv2.putText(img, "SETTLE", (tx + 92, ty - 1), METER, 0.8, QUIET, 1, cv2.LINE_AA)
    if send_secs is not None:
        cv2.rectangle(img, (tx, ty + 12), (tx + 160, ty + 20), SLATE, 1, cv2.LINE_AA)
        span = int(min(1.0, send_secs / 3.0) * 158)
        cv2.rectangle(img, (tx + 1, ty + 13), (tx + 1 + span, ty + 19), VIOLET, -1)
        cv2.putText(img, f"{send_secs:.1f}s", (tx + 168, ty + 20),
                    METER, 0.9, VIOLET, 1, cv2.LINE_AA)
    cv2.putText(img, f"{fps:.0f} fps", (x0 + w - 60, y0 + 20),
                METER, 0.8, QUIET, 1, cv2.LINE_AA)


def _row_y(i):
    return ROW0 + i * ROWH


def draw_settings(img, values):
    """Values the operator sets once and forgets, kept off the main view."""
    x0, y0, w, h = PLATE
    _panel(img, x0, y0, w, h, border=VIOLET, alpha=0.9)
    cv2.putText(img, "SETTINGS", (x0 + 20, y0 + 26), LABEL, 0.5, VIOLET, 1, cv2.LINE_AA)
    cv2.putText(img, "applied to the next pose you send", (x0 + 20, y0 + 46),
                LABEL, 0.4, QUIET, 1, cv2.LINE_AA)
    for i, (label, key, _s, _lo, _hi, unit) in enumerate(ROWS):
        y = _row_y(i)
        cv2.putText(img, label, (x0 + 20, y + 22), LABEL, 0.52, CREAM, 1, cv2.LINE_AA)
        text = f"{values[key]}{unit}"
        cv2.putText(img, text, (x0 + 300 - 9 * len(text), y + 22),
                    LABEL, 0.52, AMBER, 1, cv2.LINE_AA)
        for bx, sign in ((MINUS_X, "-"), (PLUS_X, "+")):
            cv2.rectangle(img, (bx, y - 2), (bx + STEP_W, y + 30), SLATE, 1, cv2.LINE_AA)
            cv2.putText(img, sign, (bx + 17, y + 22), LABEL, 0.6, CREAM, 1, cv2.LINE_AA)


def settings_hit(x, y):
    """Which knob a click landed on, and which way. None if it missed."""
    for i, (_l, key, step, lo, hi, _u) in enumerate(ROWS):
        ry = _row_y(i)
        if not (ry - 2 <= y <= ry + 30):
            continue
        if MINUS_X <= x <= MINUS_X + STEP_W:
            return key, -step, lo, hi
        if PLUS_X <= x <= PLUS_X + STEP_W:
            return key, step, lo, hi
    return None
