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

SEND_CMD = ["/home/eddlai/inspire_hand/soem_build/hand_set"]  # lean path ~2-3s
SETTLE_FRAMES = 5      # consecutive quiet frames before AUTO fires
SETTLE_TOL = 120       # target units counted as "not moving"
DEADBAND = 250         # ignore poses this close to the last one sent
EMA = 0.65             # smoothing against landmark jitter

send_lock = threading.Lock()
last_result = "no send yet"
auto_sync = False
last_sent = None
BTN = (10, 10, 190, 58)


def send_pose(tgt):
    global last_result
    if not send_lock.acquire(blocking=False):
        return
    def work():
        global last_result
        try:
            t0 = time.perf_counter()
            r = subprocess.run(SEND_CMD + [str(v) for v in tgt],
                               capture_output=True, text=True, timeout=40)
            dt = time.perf_counter() - t0
            out = (r.stdout + r.stderr).strip().splitlines()
            tail = out[-1][:56] if out else f"rc={r.returncode}"
            last_result = f"{dt:.1f}s {tail}"
        except Exception as e:
            last_result = f"send error: {e}"
        finally:
            send_lock.release()
    threading.Thread(target=work, daemon=True).start()


def on_mouse(event, x, y, flags, param):
    global auto_sync
    if event == cv2.EVENT_LBUTTONDOWN:
        x0, y0, x1, y1 = BTN
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
    names = ["PKY", "RNG", "MID", "IDX", "THB", "ROT"]

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
            for i, v in enumerate(tgt):
                x = 210 + i * 118
                cv2.putText(frame, f"{names[i]} {v}", (x, 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                cv2.rectangle(frame, (x, 44), (x + 100, 56), (80, 80, 80), 1)
                cv2.rectangle(frame, (x, 44), (x + int(v / 2000 * 100), 56),
                              (60, 220, 60), -1)
        else:
            quiet = 0

        x0, y0, x1, y1 = BTN
        cv2.rectangle(frame, (x0, y0), (x1, y1),
                      (40, 200, 40) if auto_sync else (60, 60, 200), -1)
        cv2.putText(frame, f"SYNC {'ON' if auto_sync else 'OFF'}",
                    (x0 + 18, y0 + 33), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2)
        if send_lock.locked():
            state, tint = "SENDING", (0, 200, 255)
        elif tgt is None:
            state, tint = "NO HAND", (150, 150, 150)
        elif quiet >= SETTLE_FRAMES:
            state, tint = "SETTLED", (60, 220, 60)
        else:
            state, tint = "MOVING", (200, 200, 60)
        cv2.putText(frame, state, (x1 + 14, y0 + 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, tint, 2)
        cv2.putText(frame, "SPACE send once | A auto | Q quit",
                    (10, frame.shape[0] - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (200, 200, 200), 1)
        cv2.putText(frame, f"robot: {last_result}", (10, frame.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - fps_t, 1e-3))
        fps_t = now
        cv2.putText(frame, f"{fps:4.1f} FPS", (frame.shape[1] - 110, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

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
