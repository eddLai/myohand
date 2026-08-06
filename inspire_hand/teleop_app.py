#!/usr/bin/env python3
"""RH56F1 Hand Teleop - MediaPipe finger tracking with robot-hand sync.

UI (English):
  [SYNC] button (click)  - toggle AUTO sync to robot hand
  SPACE - send current pose once      A - toggle AUTO sync
  Q/ESC - quit

Targets come from joint angles on MediaPipe's world landmarks, so a
rotated hand still reports the pose it is actually holding.

Where those targets go is a choice now, not a constant:

  --sink=daemon    (default) stream into handd at --rate Hz. Continuous
                   following: the pose is pushed as it changes.
  --sink=hand_set  one subprocess per pose, the path that predates the
                   daemon. On that path a pose costs two to three seconds
                   - the cost of spawning and reconnecting per pose, not
                   of the hand, which follows continuously at 500 Hz - so
                   the
                   settle gate turns itself on.
  --sink=none      dry run. The camera, the mapping and the whole window
                   work with no hand and no daemon.

The settle gate - wait for five frames within 120 units before sending -
existed only because a pose was slow. It is --settle-frames now, and
defaults to off when streaming, because waiting for the operator to hold
still is the opposite of following them.
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import mediapipe as mp

import hand_latency
import hand_mapping as hm
import hand_sink
import teleop_ui as ui

# Defaults only. Every one of these is a command-line option now: the
# settle gate exists because a pose costs two to three seconds on the
# hand_set path - a cost that path imposes, not one the hand does; the
# daemon path has no reason for it and defaults it off. Historically it
# whether that is still true depends on the trigger the daemon is running.
# A tolerance in target counts. It was set when a target count was 0.48
# of an ANGLEACT count, so the 2026-08-06 scale correction shrinks it to
# the same physical stillness: 120 old units = 58 ANGLEACT.
SETTLE_TOL = 58

sink = None                # where poses go; see hand_sink.open_sink
settle_frames = 5          # 0 disables the gate entirely
auto_sync = False
last_sent = None
cal = None                 # {feature: [min, max]} while calibrating
cal_note = ""
cal_grew = 0.0             # when the range last got bigger
CAL_QUIET = 4.0            # save once no new extreme has shown up for this long
CAL_NEED = {"curl_hi": 40, "abd": 15}   # the spread a usable window needs
PARK = [hm.T_MAX] * 6      # every joint open - the pose to leave the hand in
show_settings = False
SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teleop_settings.json")
SETTINGS = {"force": 500, "speed": 1000, "device": 4, "ema": 65}
try:
    SETTINGS.update(json.load(open(SET_PATH)))
except FileNotFoundError:
    pass

def send_pose(tgt, stamps=None):
    """Hand the pose to whichever sink is open. The sink decides whether
    that means a subprocess, a socket write, or nothing at all."""
    return sink.send(tgt, stamps)


def save_settings():
    with open(SET_PATH, "w") as f:
        json.dump(SETTINGS, f, indent=2)


def hit(rect, x, y):
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def cal_missing(c):
    """Which criteria the operator has not demonstrated yet."""
    span = {k: v[1] - v[0] for k, v in (c or {}).items()}
    return [k for k, need in CAL_NEED.items() if span.get(k, 0) < need]


def toggle_calibration():
    """Start recording the operator's range, or close it out and keep the
    windows if they actually moved far enough to define one. Calibration
    also closes itself: both hands are busy demonstrating the range, so
    asking for a second click is asking for the one thing they cannot do."""
    global cal, cal_note, cal_grew
    if cal is None:
        cal, cal_note, cal_grew = {}, "move through your full range", time.time()
        return
    if cal_missing(cal):
        cal, cal_note = None, "range too small - discarded"
        return
    # A new profile every time. The measured windows already in the file
    # are data, not settings, and a stray click on a badly lit day must
    # not be able to land on top of them.
    name = hm.save_calibration({
        "CURL_OPEN": round(cal["curl_lo"][0], 1),
        "CURL_CLOSED": round(cal["curl_hi"][1], 1),
        "THUMB_OPEN": round(cal["thumb"][0], 1),
        "THUMB_CLOSED": round(cal["thumb"][1], 1),
        "ABD_MIN": round(cal["abd"][0], 1),
        "ABD_MAX": round(cal["abd"][1], 1),
    })
    cal, cal_note = None, f"saved as profile {name}"


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


def build_parser():
    ap = argparse.ArgumentParser(
        description="RH56F1 teleop. Nothing here is wired to a hand until "
                    "--sink says so.")
    ap.add_argument("--device", type=int, default=4)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--sink", choices=("daemon", "hand_set", "none"),
                    default="daemon",
                    help="daemon: stream into handd (continuous following). "
                         "hand_set: one subprocess per pose, the pre-daemon "
                         "path. none: dry run, no hand needed.")
    ap.add_argument("--socket", default=None,
                    help="handd socket (default $HAND_SOCKET)")
    ap.add_argument("--rate", type=float, default=50.0,
                    help="Hz at which the daemon sink pushes targets")
    ap.add_argument("--deadband", type=int, default=None,
                    help="target units a pose must move before it is resent; "
                         "the sink picks a sensible default")
    ap.add_argument("--settle-frames", type=int, default=None,
                    help="quiet frames required before AUTO fires. 0 turns "
                         "the gate off, which is what continuous following "
                         "means. Default depends on the sink.")
    ap.add_argument("--settle-tol", type=int, default=SETTLE_TOL,
                    help="target units still counted as 'not moving'")
    ap.add_argument("--profile", default=None,
                    help="calibration profile to use; default is the active "
                         "one (python3 hand_mapping.py lists them)")
    ap.add_argument("--headless", action="store_true",
                    help="run the camera, mapping and sink with no window. "
                         "The whole vision chain over SSH, no display needed.")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop after N frames and print a summary; 0 = run "
                         "until q")
    return ap


def main():
    global auto_sync, last_sent, show_settings, sink, settle_frames, SETTLE_TOL
    args = build_parser().parse_args()

    SETTLE_TOL = args.settle_tol
    if args.profile:
        if hm.load_calibration(args.profile) is None:
            print(f"no calibration profile '{args.profile}' - "
                  f"run `python3 hand_mapping.py` to see what there is",
                  file=sys.stderr)
            return 1
    print(f"calibration profile: {hm.ACTIVE_PROFILE or '(module defaults)'}")
    try:
        sink = hand_sink.open_sink(
            args.sink, socket_path=args.socket, rate_hz=args.rate,
            deadband=args.deadband, force=SETTINGS["force"],
            speed=SETTINGS["speed"])
    except Exception as e:                                  # noqa: BLE001
        # Say which sink failed and offer the one that needs nothing, rather
        # than falling back silently to a different thing being measured.
        print(f"could not open the '{args.sink}' sink: {e}\n"
              f"To work on the vision chain without a hand: --sink=none",
              file=sys.stderr)
        return 1
    settle_frames = (args.settle_frames if args.settle_frames is not None
                     else hand_sink.settle_frames_default(sink.name))
    print(f"sink={sink.name}  settle_frames={settle_frames}  "
          f"deadband={sink.deadband}"
          + (f"  rate={args.rate:g} Hz" if sink.name == "daemon" else ""))

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
    if not args.headless:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, args.width + 160, args.height + 90)
        cv2.moveWindow(win, 40, 60)
        cv2.setMouseCallback(win, on_mouse)
    else:
        # AUTO with no window to click: headless is for exercising the chain
        # end to end, and a run that never sends is not exercising it.
        auto_sync = True

    ema = None
    quiet = 0
    fps_t, fps = time.time(), 0.0
    stamps = hand_latency.Stamps()
    frames = seen = 0
    t_start = time.time()

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
        stamps.frame()          # the latency ruler starts at the frame
        frames += 1
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
            seen += 1
            stamps.mapped()     # targets are ready; the rest is the daemon's
        else:
            quiet = 0

        if cal is not None and not cal_missing(cal):
            # never time out and discard: the range stays open until the
            # operator has actually shown it, then settles on its own
            if time.time() - cal_grew > CAL_QUIET:
                toggle_calibration()
        busy = sink.busy
        ui.draw_gauge(frame, tgt, busy, sink.actual)
        ui.draw_button(frame, ui.SYNC_BTN, auto_sync,
                       "SYNC ON" if auto_sync else "SYNC OFF")
        ui.draw_button(frame, ui.CAL_BTN, cal is not None,
                       "CALIBRATING" if cal is not None else "CALIBRATE", ui.VIOLET,
                       enabled=not busy)
        ui.draw_button(frame, ui.PARK_BTN, False, "OPEN HAND", enabled=not busy)
        ui.draw_button(frame, ui.SET_BTN, show_settings, "SETTINGS", ui.VIOLET)
        if show_settings:
            ui.draw_settings(frame, SETTINGS)
        elapsed = sink.elapsed()
        if cal is not None:
            missing = cal_missing(cal)
            headline = "Calibrating"
            if not missing:
                hint = (f"saving in {max(0.0, CAL_QUIET - (time.time() - cal_grew)):.0f} s - "
                        "keep going if you have more range to show")
            else:
                span = {k: v[1] - v[0] for k, v in cal.items()}
                need = ["fingers: open wide, then a full fist "
                        f"({span.get('curl_hi', 0):.0f} of {CAL_NEED['curl_hi']} deg)"
                        if "curl_hi" in missing else "",
                        "thumb: tuck it in, then splay it out "
                        f"({span.get('abd', 0):.0f} of {CAL_NEED['abd']} deg)"
                        if "abd" in missing else ""]
                hint = "   ".join(n for n in need if n)
            tone = ui.VIOLET
        elif busy:
            headline, hint, tone = "Hand moving", "mirroring the pose you held", ui.VIOLET
        elif tgt is None:
            headline, hint, tone = "Show your hand", "hold it in view of the camera", ui.CREAM
        elif quiet >= settle_frames:
            headline, hint, tone = (
                ("Following" if settle_frames == 0 else "Sending",
                 f"streaming to {sink.name}" if settle_frames == 0
                 else "auto sync is on", ui.AMBER) if auto_sync
                else ("Ready", "press space to send this pose", ui.AMBER))
        else:
            headline, hint, tone = "Hold still", "the pose sends once it settles", ui.CREAM
        now = time.time()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - fps_t, 1e-3))
        fps_t = now
        progress = 1.0 if settle_frames == 0 else min(1.0, quiet / settle_frames)
        ui.draw_rail(frame, headline, hint, tone, progress,
                     elapsed, cal_note or sink.last_result, fps,
                     "space  send      q  quit")

        # With the gate off this fires every frame and the sink decides what
        # is worth sending; with the gate on it waits for stillness, which is
        # what a two-to-three-second pose demands.
        if (tgt and auto_sync and quiet >= settle_frames and not sink.busy
                and (last_sent is None
                     or max(abs(a - b) for a, b in zip(tgt, last_sent))
                     > sink.deadband)):
            last_sent = tgt[:]
            send_pose(tgt, stamps)

        if args.max_frames and frames >= args.max_frames:
            break
        if args.headless:
            continue
        cv2.imshow(win, frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord(" ") and tgt:
            last_sent = tgt[:]
            send_pose(tgt, stamps)
        elif k == ord("a"):
            auto_sync = not auto_sync
        elif k == ord("c"):
            toggle_calibration()
        elif k == ord("o"):
            send_pose(PARK)
        elif k == ord("s"):
            show_settings = not show_settings

    cap.release()
    if not args.headless:
        cv2.destroyAllWindows()
    dt = max(time.time() - t_start, 1e-6)
    print(f"{frames} frames in {dt:.1f}s = {frames / dt:.1f} FPS; "
          f"a hand was in {seen} of them ({100.0 * seen / max(frames, 1):.0f}%); "
          f"{sink.sent} pose(s) reached the {sink.name} sink")
    sink.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
