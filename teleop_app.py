#!/usr/bin/env python3
"""RH56F1 Hand Teleop - MediaPipe finger tracking with robot-hand sync.

UI (English):
  [SYNC] button (click)  - toggle AUTO sync to robot hand
  SPACE - send current pose once      A - toggle AUTO sync
  Q/ESC - quit

Targets come from joint angles on MediaPipe's world landmarks, so a
rotated hand still reports the pose it is actually holding. Because one
pose costs the hand two to three seconds, AUTO waits for the pose to
settle and sends what the operator meant to hold, not what passed by
mid-transition.
"""
import argparse, json, os, re, subprocess, threading, time
import cv2
import mediapipe as mp

import hand_mapping as hm
import teleop_ui as ui

SEND_CMD = ["/home/eddlai/inspire_hand/soem_build/hand_set"]  # lean path ~2-3s
SETTLE_FRAMES = 5      # consecutive quiet frames before AUTO fires
SETTLE_TOL = 120       # target units counted as "not moving"
DEADBAND = 250         # ignore poses this close to the last one sent

send_lock = threading.Lock()
last_result = "hand idle"
send_started = None
auto_sync = False
last_sent = None
cal = None                 # {feature: [min, max]} while calibrating
cal_note = ""
cal_grew = 0.0             # when the range last got bigger
CAL_QUIET = 4.0            # save once no new extreme has shown up for this long
CAL_MAX = 45.0             # and give up waiting after this
actual = None              # where the hand reported it got to, in target units
PARK = [2000] * 6          # every joint open - the pose to leave the hand in
show_settings = False
SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teleop_settings.json")
SETTINGS = {"force": 500, "speed": 1000, "device": 4, "ema": 65}
try:
    SETTINGS.update(json.load(open(SET_PATH)))
except FileNotFoundError:
    pass

def send_pose(tgt):
    global last_result, send_started
    if not send_lock.acquire(blocking=False):
        return
    def work():
        global last_result, send_started
        try:
            t0 = send_started = time.perf_counter()
            r = subprocess.run(SEND_CMD + [str(v) for v in tgt]
                               + [str(SETTINGS["force"]), str(SETTINGS["speed"])],
                               capture_output=True, text=True, timeout=40)
            dt = time.perf_counter() - t0
            out = (r.stdout + r.stderr).strip().splitlines()
            guarded = any("guard" in ln for ln in out)
            read_back(out)
            last_result = (f"held back a clash  {dt:.1f}s" if guarded
                           else f"pose reached the hand  {dt:.1f}s")
        except Exception as e:
            last_result = "hand did not answer - check its power and the RJ45 link"
        finally:
            send_started = None
            send_lock.release()
    threading.Thread(target=work, daemon=True).start()


def read_back(lines):
    """hand_set already reports where the joints ended up; keep it instead
    of throwing the line away, so the gauge can show both readings."""
    global actual
    for ln in lines:
        m = re.search(r"ANG=\[([-\d ]+)\]", ln)
        if m:
            actual = [hm.target_from_angle(v) for v in m.group(1).split()]
            return


def save_settings():
    with open(SET_PATH, "w") as f:
        json.dump(SETTINGS, f, indent=2)


def hit(rect, x, y):
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def cal_wide_enough(c):
    span = {k: v[1] - v[0] for k, v in (c or {}).items()}
    return span.get("curl_hi", 0) >= 40 and span.get("abd", 0) >= 15


def toggle_calibration():
    """Start recording the operator's range, or close it out and keep the
    windows if they actually moved far enough to define one. Calibration
    also closes itself: both hands are busy demonstrating the range, so
    asking for a second click is asking for the one thing they cannot do."""
    global cal, cal_note, cal_grew
    if cal is None:
        cal, cal_note, cal_grew = {}, "move through your full range", time.time()
        return
    if not cal_wide_enough(cal):
        cal, cal_note = None, "range too small - discarded"
        return
    hm.save_calibration({
        "CURL_OPEN": round(cal["curl_lo"][0], 1),
        "CURL_CLOSED": round(cal["curl_hi"][1], 1),
        "THUMB_OPEN": round(cal["thumb"][0], 1),
        "THUMB_CLOSED": round(cal["thumb"][1], 1),
        "ABD_MIN": round(cal["abd"][0], 1),
        "ABD_MAX": round(cal["abd"][1], 1),
    })
    cal, cal_note = None, "calibrated and saved"


def on_mouse(event, x, y, flags, param):
    global auto_sync, show_settings
    if event == cv2.EVENT_LBUTTONDOWN:
        if hit(ui.SYNC_BTN, x, y):
            auto_sync = not auto_sync
        elif hit(ui.CAL_BTN, x, y):
            toggle_calibration()
        elif hit(ui.PARK_BTN, x, y):
            send_pose(PARK)
        elif hit(ui.SET_BTN, x, y):
            show_settings = not show_settings
        elif show_settings:
            knob = ui.settings_hit(x, y)
            if knob:
                key, delta, lo, hi = knob
                SETTINGS[key] = max(lo, min(hi, SETTINGS[key] + delta))
                save_settings()


def main():
    global auto_sync, last_sent, show_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=4)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    args = ap.parse_args()

    SETTINGS["device"] = args.device if args.device != 4 else SETTINGS["device"]
    cap = cv2.VideoCapture(SETTINGS["device"])
    opened_device = SETTINGS["device"]
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                     min_detection_confidence=0.6,
                                     min_tracking_confidence=0.5)
    draw, styles = mp.solutions.drawing_utils, mp.solutions.drawing_styles
    win = "RH56F1 Hand Teleop"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, args.width + 160, args.height + 90)
    cv2.moveWindow(win, 40, 60)
    cv2.setMouseCallback(win, on_mouse)

    ema = None
    quiet = 0
    fps_t, fps = time.time(), 0.0

    while True:
        if SETTINGS["device"] != opened_device:     # follow the settings plate
            cap.release()
            cap = cv2.VideoCapture(SETTINGS["device"])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
            opened_device, ema = SETTINGS["device"], None
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        frame = cv2.flip(frame, 1)
        res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        tgt = None

        if res.multi_hand_landmarks and res.multi_hand_world_landmarks:
            draw.draw_landmarks(frame, res.multi_hand_landmarks[0],
                                mp.solutions.hands.HAND_CONNECTIONS,
                                styles.get_default_hand_landmarks_style(),
                                styles.get_default_hand_connections_style())
            raw = hm.pose_from_world_landmarks(res.multi_hand_world_landmarks[0].landmark)
            if ema is None:
                ema = raw[:]
            else:
                w = SETTINGS["ema"] / 100.0
                ema = [int(w * e + (1 - w) * r) for e, r in zip(ema, raw)]
            if cal is not None:
                grew = False
                for k, v in hm.raw_features(res.multi_hand_world_landmarks[0].landmark).items():
                    lo, hi = cal.get(k, (v, v))
                    if v < lo - 0.5 or v > hi + 0.5:
                        grew = True
                    cal[k] = (min(lo, v), max(hi, v))
                if grew:
                    cal_grew = time.time()
            moved = max(abs(a - b) for a, b in zip(ema, raw))
            quiet = quiet + 1 if moved < SETTLE_TOL else 0
            tgt = ema[:]
        else:
            quiet = 0

        if cal is not None:
            idle = time.time() - cal_grew
            if (idle > CAL_QUIET and cal_wide_enough(cal)) or idle > CAL_MAX:
                toggle_calibration()          # closes itself once you stop finding new range
        busy = send_lock.locked()
        ui.draw_gauge(frame, tgt, busy, actual)
        ui.draw_button(frame, ui.SYNC_BTN, auto_sync,
                       "SYNC ON" if auto_sync else "SYNC OFF")
        ui.draw_button(frame, ui.CAL_BTN, cal is not None,
                       "CALIBRATING" if cal is not None else "CALIBRATE", ui.VIOLET,
                       enabled=not busy)
        ui.draw_button(frame, ui.PARK_BTN, False, "OPEN HAND", enabled=not busy)
        ui.draw_button(frame, ui.SET_BTN, show_settings, "SETTINGS", ui.VIOLET)
        if show_settings:
            ui.draw_settings(frame, SETTINGS)
        elapsed = (time.perf_counter() - send_started) if send_started else None
        if cal is not None:
            n = cal.get("abd", (0, 0))
            headline = "Calibrating"
            if cal_wide_enough(cal):
                hint = (f"saving in {max(0.0, CAL_QUIET - (time.time() - cal_grew)):.0f} s - "
                        "keep going if you have more range to show")
            else:
                hint = ("open wide, make a fist, tuck and splay the thumb - "
                        f"thumb spread so far {n[1] - n[0]:.0f} deg")
            tone = ui.VIOLET
        elif busy:
            headline, hint, tone = "Hand moving", "mirroring the pose you held", ui.VIOLET
        elif tgt is None:
            headline, hint, tone = "Show your hand", "hold it in view of the camera", ui.CREAM
        elif quiet >= SETTLE_FRAMES:
            headline, hint, tone = (("Sending", "auto sync is on", ui.AMBER) if auto_sync
                                    else ("Ready", "press space to send this pose", ui.AMBER))
        else:
            headline, hint, tone = "Hold still", "the pose sends once it settles", ui.CREAM
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - fps_t, 1e-3))
        fps_t = now
        ui.draw_rail(frame, headline, hint, tone, min(1.0, quiet / SETTLE_FRAMES),
                     elapsed, cal_note or last_result, fps,
                     "space  send      q  quit")

        if (tgt and auto_sync and quiet >= SETTLE_FRAMES
                and not send_lock.locked()
                and (last_sent is None
                     or max(abs(a - b) for a, b in zip(tgt, last_sent)) > DEADBAND)):
            last_sent = tgt[:]
            send_pose(tgt)

        cv2.imshow(win, frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord(" ") and tgt:
            last_sent = tgt[:]
            send_pose(tgt)
        elif k == ord("a"):
            auto_sync = not auto_sync
        elif k == ord("c"):
            toggle_calibration()
        elif k == ord("o"):
            send_pose(PARK)
        elif k == ord("s"):
            show_settings = not show_settings

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
