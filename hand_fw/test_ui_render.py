#!/usr/bin/env python3
"""Does the instrument panel draw, on a machine with no display?

teleop_ui is the part of the vision chain that needs neither a camera nor
a hand, so it is the part that can be checked anywhere - including over
SSH on the board, where there is no X server to open a window on. Every
state the panel can be in gets rendered into an image and written out, so
a broken layout shows up as a crash here rather than as a blank rail in
front of an operator.

    python3 test_ui_render.py [--out /tmp/teleop_ui]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                                          # noqa: E402
import cv2                                                  # noqa: E402

import teleop_ui as ui                                      # noqa: E402

fails = 0

STATES = [
    # name,            targets,        busy, actual,          headline, hint
    ("no-hand",        None,           False, None,           "Show your hand",
     "hold it in view of the camera"),
    # target counts, ANGLEACT scale (~890 closed .. 1850 open)
    ("following",      [1610] * 6,     False, [1586] * 6,     "Following",
     "streaming to daemon"),
    ("guard-split",    [1034] * 4 + [1178, 1610], False,
     [1466] * 4 + [1178, 1610],                              "Hold still",
     "the pose sends once it settles"),
    ("busy",           [1274] * 6,     True,  [1850] * 6,     "Hand moving",
     "mirroring the pose you held"),
    ("calibrating",    [1370] * 6,     False, None,           "Calibrating",
     "fingers: open wide, then a full fist (12 of 40 deg)"),
]


def check(name, cond, detail=""):
    global fails
    print(f"{name:<52s} {'ok' if cond else 'FAIL'}"
          f"{'  ' + detail if detail and not cond else ''}")
    if not cond:
        fails += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/teleop_ui")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    settings = {"force": 500, "speed": 1000, "device": 4, "ema": 65}

    for name, tgt, busy, actual, headline, hint in STATES:
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        frame[:] = (40, 30, 25)
        try:
            ui.draw_gauge(frame, tgt, busy, actual)
            ui.draw_button(frame, ui.SYNC_BTN, True, "SYNC ON")
            ui.draw_button(frame, ui.CAL_BTN, name == "calibrating",
                           "CALIBRATING" if name == "calibrating" else "CALIBRATE",
                           ui.VIOLET, enabled=not busy)
            ui.draw_button(frame, ui.PARK_BTN, False, "OPEN HAND", enabled=not busy)
            ui.draw_button(frame, ui.SET_BTN, False, "SETTINGS", ui.VIOLET)
            if name == "busy":
                ui.draw_settings(frame, settings)
            ui.draw_rail(frame, headline, hint,
                         ui.VIOLET if busy else ui.AMBER, 0.6,
                         1.7 if busy else None, "last result here", 27.9,
                         "space  send      q  quit")
            path = os.path.join(args.out, f"{name}.png")
            cv2.imwrite(path, frame)
            drawn = int((frame != np.array([40, 30, 25], dtype=np.uint8)).any(axis=2).sum())
            check(f"{name}: renders and marks the frame",
                  os.path.exists(path) and drawn > 5000, f"{drawn} pixels drawn")
        except Exception as e:                               # noqa: BLE001
            check(f"{name}: renders", False, repr(e))

    # the click targets have to stay inside the frame, or a button exists
    # that nobody can press
    for label, rect in (("SYNC", ui.SYNC_BTN), ("CAL", ui.CAL_BTN),
                        ("PARK", ui.PARK_BTN), ("SET", ui.SET_BTN)):
        x0, y0, x1, y1 = rect
        check(f"the {label} button is inside the window",
              0 <= x0 < x1 <= 960 + 160 and 0 <= y0 < y1 <= 540 + 90, str(rect))

    print(f"\nimages in {args.out}")
    print("FAILURES PRESENT" if fails else "all checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
