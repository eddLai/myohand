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
import argparse, subprocess, threading, time
import cv2
import mediapipe as mp

import hand_mapping as hm
import teleop_ui as ui

SEND_CMD = ["/home/eddlai/inspire_hand/soem_build/hand_set"]  # lean path ~2-3s
SETTLE_FRAMES = 5      # consecutive quiet frames before AUTO fires
SETTLE_TOL = 120       # target units counted as "not moving"
DEADBAND = 250         # ignore poses this close to the last one sent
EMA = 0.65             # smoothing against landmark jitter

send_lock = threading.Lock()
last_result = "hand idle"
send_started = None
auto_sync = False
last_sent = None

def send_pose(tgt):
    global last_result, send_started
    if not send_lock.acquire(blocking=False):
        return
    def work():
        global last_result, send_started
        try:
            t0 = send_started = time.perf_counter()
            r = subprocess.run(SEND_CMD + [str(v) for v in tgt],
                               capture_output=True, text=True, timeout=40)
            dt = time.perf_counter() - t0
            out = (r.stdout + r.stderr).strip().splitlines()
            guarded = any("guard" in ln for ln in out)
            last_result = (f"held back a clash  {dt:.1f}s" if guarded
                           else f"pose reached the hand  {dt:.1f}s")
        except Exception as e:
            last_result = f"hand did not answer: {e}"
        finally:
            send_started = None
            send_lock.release()
    threading.Thread(target=work, daemon=True).start()


def on_mouse(event, x, y, flags, param):
    global auto_sync
    if event == cv2.EVENT_LBUTTONDOWN:
        x0, y0, x1, y1 = ui.SYNC_BTN
        if x0 <= x <= x1 and y0 <= y <= y1:
            auto_sync = not auto_sync


def main():
    global auto_sync, last_sent
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=4)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.device)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    hands = mp.solutions.hands.Hands(max_num_hands=1, model_complexity=0,
                                     min_detection_confidence=0.6,
                                     min_tracking_confidence=0.5)
    draw, styles = mp.solutions.drawing_utils, mp.solutions.drawing_styles
    win = "RH56F1 Hand Teleop"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)

    ema = None
    quiet = 0
    fps_t, fps = time.time(), 0.0

    while True:
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
                ema = [int(EMA * e + (1 - EMA) * r) for e, r in zip(ema, raw)]
            moved = max(abs(a - b) for a, b in zip(ema, raw))
            quiet = quiet + 1 if moved < SETTLE_TOL else 0
            tgt = ema[:]
        else:
            quiet = 0

        busy = send_lock.locked()
        ui.draw_gauge(frame, tgt, busy)
        ui.draw_sync(frame, auto_sync)
        elapsed = (time.perf_counter() - send_started) if send_started else None
        if busy:
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
                     elapsed, last_result, fps,
                     "space  send now      a  auto sync      q  quit")

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

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
