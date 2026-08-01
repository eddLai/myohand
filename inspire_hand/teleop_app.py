#!/usr/bin/env python3
"""RH56F1 Hand Teleop - MediaPipe finger tracking with robot-hand sync.

UI (English):
  [SYNC] button (click)  - toggle AUTO sync to robot hand
  SPACE - send current pose once      A - toggle AUTO sync
  Q/ESC - quit
Robot mapping: 0=closed .. 2000=open per DOF, thumb-collision guard on.
"""
import argparse, subprocess, threading, time
import cv2
import mediapipe as mp

SEND_CMD = ["/home/eddlai/inspire_hand/soem_build/hand_set"]  # lean path ~2-3s
# (hand_api.py pose is the full-featured path but costs 10-20s per pose)
FINGERS = {"pinky": (17, 20), "ring": (13, 16), "middle": (9, 12), "index": (5, 8)}
R_MIN, R_MAX = 1.05, 1.85          # flexion ratio range for fingers
T_MIN, T_MAX = 300, 2000           # robot target clamp (never full crush)
TH_MIN = 500                       # thumb-bend floor (tracked)
ROT_MIN = 700                      # thumb-rot floor (tracked)
GUARD_FINGER, GUARD_THUMB = 700, 1400  # fist-pose collision guard
GUARD_IDX_OPEN, GUARD_ROT = 800, 1200  # rot may sweep in only if index open

send_lock = threading.Lock()
last_result = "no send yet"
auto_sync = False
last_sent = None
last_sent_time = 0.0


def dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


def pose_from_landmarks(lm):
    """Return [pinky, ring, middle, index, thumb_bend, thumb_rot] targets."""
    w = lm[0]
    tgt = []
    for name in ("pinky", "ring", "middle", "index"):
        mcp, tip = FINGERS[name]
        r = dist(lm[tip], w) / max(dist(lm[mcp], w), 1e-6)
        n = min(1.0, max(0.0, (r - R_MIN) / (R_MAX - R_MIN)))
        tgt.append(int(T_MIN + n * (T_MAX - T_MIN)))
    # thumb bend from flexion ratio
    r = dist(lm[4], w) / max(dist(lm[2], w), 1e-6)
    n = min(1.0, max(0.0, (r - 1.10) / (1.50 - 1.10)))
    thumb = int(TH_MIN + n * (T_MAX - TH_MIN))
    # thumb rotation from abduction: thumb-tip to index-MCP, palm-normalized
    r2 = dist(lm[4], lm[5]) / max(dist(lm[0], lm[5]), 1e-6)
    n2 = min(1.0, max(0.0, (r2 - 0.30) / (0.75 - 0.30)))
    rot = int(ROT_MIN + n2 * (T_MAX - ROT_MIN))
    # layered collision guards (C layer rejects index<600 & thumb<600 anyway)
    if sorted(tgt)[1] < GUARD_FINGER:          # fist-like: keep thumb clear
        thumb = max(thumb, GUARD_THUMB)
        rot = max(rot, GUARD_ROT)
    if tgt[3] < GUARD_IDX_OPEN:                # index curled: no palm sweep
        rot = max(rot, GUARD_ROT)
    tgt.append(thumb)
    tgt.append(rot)
    return tgt


def send_pose(tgt):
    global last_result
    if not send_lock.acquire(blocking=False):
        return
    def work():
        global last_result
        try:
            r = subprocess.run(SEND_CMD + [str(v) for v in tgt],
                               capture_output=True, text=True, timeout=40)
            out = (r.stdout + r.stderr).strip().splitlines()
            last_result = out[-1][:70] if out else f"rc={r.returncode}"
        except Exception as e:
            last_result = f"send error: {e}"
        finally:
            send_lock.release()
    threading.Thread(target=work, daemon=True).start()


BTN = (10, 10, 190, 58)


def on_mouse(event, x, y, flags, param):
    global auto_sync
    if event == cv2.EVENT_LBUTTONDOWN:
        x0, y0, x1, y1 = BTN
        if x0 <= x <= x1 and y0 <= y <= y1:
            auto_sync = not auto_sync


def main():
    global auto_sync, last_sent, last_sent_time
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
    draw = mp.solutions.drawing_utils
    styles = mp.solutions.drawing_styles
    win = "RH56F1 Hand Teleop"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    fps_t, fps = time.time(), 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        frame = cv2.flip(frame, 1)
        res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        tgt = None
        if not hasattr(main, "ema"):
            main.ema = None
        if res.multi_hand_landmarks:
            hl = res.multi_hand_landmarks[0]
            draw.draw_landmarks(frame, hl, mp.solutions.hands.HAND_CONNECTIONS,
                                styles.get_default_hand_landmarks_style(),
                                styles.get_default_hand_connections_style())
            raw = pose_from_landmarks(hl.landmark)
            if main.ema is None:
                main.ema = raw[:]
            else:                       # EMA smoothing against landmark jitter
                main.ema = [int(0.7 * e + 0.3 * r) if r >= 0 else r
                            for e, r in zip(main.ema, raw)]
            tgt = main.ema[:]
            names = ["PKY", "RNG", "MID", "IDX", "THB", "ROT"]
            for i, v in enumerate(tgt[:6]):
                x = 210 + i * 118
                cv2.putText(frame, f"{names[i]} {v}", (x, 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                cv2.rectangle(frame, (x, 44), (x + 100, 56), (80, 80, 80), 1)
                cv2.rectangle(frame, (x, 44), (x + int(v / 2000 * 100), 56),
                              (60, 220, 60), -1)
        # SYNC button
        x0, y0, x1, y1 = BTN
        color = (40, 200, 40) if auto_sync else (60, 60, 200)
        cv2.rectangle(frame, (x0, y0), (x1, y1), color, -1)
        cv2.putText(frame, f"SYNC {'ON' if auto_sync else 'OFF'}",
                    (x0 + 18, y0 + 33), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2)
        cv2.putText(frame, "SPACE send once | A auto | Q quit",
                    (10, frame.shape[0] - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (200, 200, 200), 1)
        cv2.putText(frame, f"robot: {last_result}",
                    (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255), 1)
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - fps_t, 1e-3))
        fps_t = now
        cv2.putText(frame, f"{fps:4.1f} FPS", (frame.shape[1] - 110, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

        if tgt and auto_sync and not send_lock.locked():
            changed = (last_sent is None or
                       max(abs(a - b) for a, b in zip(tgt[:6], last_sent[:6])) > 250)
            if changed and now - last_sent_time > 1.0:
                last_sent, last_sent_time = tgt[:], now
                send_pose(tgt)

        cv2.imshow(win, frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord(" ") and tgt:
            last_sent, last_sent_time = tgt[:], now
            send_pose(tgt)
        elif k == ord("a"):
            auto_sync = not auto_sync

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
