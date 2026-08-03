"""Render the overlay over a still frame so the layout can be judged."""
import cv2, numpy as np, teleop_ui as ui

base = cv2.imread("/tmp/now2.jpg")
if base is None:
    base = np.full((540, 960, 3), 40, np.uint8)
base = cv2.resize(base, (960, 540))

states = [
    ("Hold still", "the pose sends once it settles", ui.CREAM, 0.4, None,
     [1400, 1500, 1600, 1500, 1200, 1500], False, False, "hand idle"),
    ("Ready", "press space to send this pose", ui.AMBER, 1.0, None,
     [400, 380, 1900, 420, 1500, 1800], False, True, "pose reached the hand  2.4s"),
    ("Hand moving", "mirroring the pose you held", ui.VIOLET, 1.0, 1.8,
     [400, 380, 1900, 420, 1500, 1800], True, True, "pose reached the hand  2.4s"),
    ("Show your hand", "hold it in view of the camera", ui.CREAM, 0.0, None,
     None, False, False, "hand idle"),
]
tiles = []
for head, hint, tone, sf, secs, tgt, busy, sync, tele in states:
    f = base.copy()
    ui.draw_gauge(f, tgt, busy, [1904, 1866, 1870, 12, 1010, 1985] if tgt else None)
    ui.draw_button(f, ui.SYNC_BTN, sync, "SYNC ON" if sync else "SYNC OFF")
    ui.draw_button(f, ui.CAL_BTN, False, "CALIBRATE", ui.VIOLET, enabled=not busy)
    ui.draw_button(f, ui.PARK_BTN, False, "OPEN HAND", enabled=not busy)
    ui.draw_button(f, ui.SET_BTN, sync, "SETTINGS", ui.VIOLET)
    if sync:
        ui.draw_settings(f, {"force": 500, "speed": 1000, "device": 4, "ema": 65})
    ui.draw_rail(f, head, hint, tone, sf, secs, tele, 29.4,
                 "space  send      q  quit")
    tiles.append(f)
cv2.imwrite("/tmp/ui_preview.png",
            np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))
print("wrote /tmp/ui_preview.png")
