#!/usr/bin/env python3
"""RH56F1 Hand Teleop - MediaPipe finger tracking with robot-hand sync.

UI (English):
  [SYNC] button (click)  - toggle AUTO sync to robot hand
  SPACE - send current pose once      A - toggle AUTO sync
  C - run the guided calibration      Q/ESC - quit

The window opens even when the camera is missing: a dead camera leaves
the panel on a placeholder and retries in the background, or switch
device in SETTINGS. Whether the hand itself is reachable is reported by
the sink (see --sink below) rather than a separate probe.

Targets come from joint angles on MediaPipe's world landmarks, so a
rotated hand still reports the pose it is actually holding. A trust gate
(hand_mapping.thumb_trust) holds the two thumb axes at their last
believed pose on frames where MediaPipe is guessing rather than seeing -
edge-on to the camera, mid-flip on the handedness label, or the thumb
drawn from behind the palm.

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
import argparse, json, os, signal, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "camera")))
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "hand_fw")))
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "filter")))

import cv2
import numpy as np
import mediapipe as mp

import hand_filter
import hand_latency
import run_log
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

stop = False               # set by SIGINT/SIGTERM; the loop checks it
sink = None                # where poses go; see hand_sink.open_sink
settle_frames = 5          # 0 disables the gate entirely
auto_sync = False
# 'f' flips the filter out of the path so the two can be compared on the same
# hand in the same light. Bypass is the OLD behaviour exactly: raw targets and
# one max-over-six-axes deadband, which is what makes it a fair comparison
# rather than just a louder version of the new one.
use_filter = True
BYPASS_DEADBAND = 12       # what hand_sink.Sink.deadband was
CAM_RETRY = 3.0            # seconds between reopen attempts while offline
last_sent = None
cal_note = ""              # what the last calibration attempt did
cal_request = False        # a click asks; the loop owns the camera and acts
CAL_TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "nn", "thumb_calib_ui.py")
CAL_HOLD = 5.0             # seconds held per pose, passed through to the tool
PARK = [hm.T_MAX] * 6      # every joint open - the pose to leave the hand in
show_settings = False
SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teleop_settings.json")
SETTINGS = {"force": 500, "speed": 1000, "device": 4, "deadband_deg": 1.5}
try:
    SETTINGS.update(json.load(open(SET_PATH)))
except FileNotFoundError:
    pass

def on_signal(sig, frame):
    """Ctrl+C and SIGTERM ask the loop to stop rather than tearing the
    process down where it stands.

    Without this the camera was never released: cap.release() sat after the
    loop and an interrupt never reached it, so /dev/video0 stayed held by a
    dead-but-not-gone process and the next run blocked on opening it. That
    is also why a wrapper cannot fix this from outside - an OpenCV read does
    not always return control to Python in time for a handler, so the flag
    has to be checked by the loop itself and the release has to be in a
    finally."""
    global stop
    _ = sig, frame
    stop = True


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


def toggle_calibration():
    """Ask for a calibration run. A flag and nothing else: the tool that
    runs one needs the camera, and the camera belongs to the main loop."""
    global cal_request
    cal_request = True


def run_calibration(device, open_camera, cap):
    """Hand the camera to nn/thumb_calib_ui.py, then take it back.

    This used to be measured here. A click opened a window on the
    operator's free movement and kept its extremes, which is how the
    2026-08-06 profile came to have a THUMB_CLOSED measured from the thumb
    ROTATING rather than folding: in the five seconds meant as a curl,
    opposition travelled 124 degrees and flexion 60. Nothing in this file
    could have noticed. Free movement carries no claim about which joint
    is moving, so there is nothing to check it against.

    The tool does carry one. It names each pose on screen, holds it, and
    refuses to save when a channel that should have stayed still did not.
    Keeping a second copy of the pose list and that check here would give
    the project two definitions of one procedure, and the two would drift;
    the click hands the camera over instead and waits.

    The cost is visible rather than hidden: this window stops for the
    length of a calibration and a second one appears in front of it.
    """
    global auto_sync, cal_note, last_sent
    # Nobody drives the hand for the next minute, and the operator needs
    # both hands to pose. Whatever it was gripping, it should not be left
    # gripping it unattended.
    try:
        send_pose(PARK)
    except Exception as e:      # noqa: BLE001 - parking is a courtesy, and a
        # sink that cannot take it is no reason to refuse a calibration
        print(f"could not open the hand before calibrating: {e}", file=sys.stderr)
    # forget where it was told to go: the deadband compares the next
    # target against last_sent, so a stale one from before the calibration
    # can swallow the first target after it. The hand then sits still while
    # SYNC reads ON, which looks like the sync broke.
    last_sent = None
    cap.release()               # one process at a time on /dev/video*
    name = time.strftime("session-%Y%m%d-%H%M%S")
    # what counts as success is a profile that was not there a moment ago.
    # Asking only whether the name loads says yes to a name that already
    # existed, and reports a refused calibration as a saved one.
    before = set(hm.list_profiles()[1])
    try:
        r = subprocess.run([sys.executable, CAL_TOOL, str(device),
                            str(CAL_HOLD), "--save=" + name])
        if r.returncode != 0:
            cal_note = f"calibration did not finish (exit {r.returncode})"
        elif name in before or hm.load_calibration(name) is None:
            # the tool ran and declined to save - a contaminated recording
            # is refused there, and the reason is on the terminal
            cal_note = "calibration refused - see the terminal"
        else:
            cal_note = f"saved as profile {name}"
            # A window is measured in order to be used, and the operator is
            # standing in front of the camera having just demonstrated it.
            # Making them find SYNC before the hand moves again reads as the
            # calibration not having taken. The hand was parked open on the
            # way in, so what it resumes from is known.
            auto_sync = True
    except Exception as e:      # noqa: BLE001 - losing the camera is worse
        cal_note = f"could not run the calibration tool: {e}"
    print(f"calibration profile: {hm.ACTIVE_PROFILE or '(module defaults)'}")
    # the child only just let go of /dev/video*, and the first open after
    # that can still come back closed. Without this the loop falls into the
    # offline placeholder and waits out CAM_RETRY, so a clean handover
    # looks to the operator like the camera broke.
    for _ in range(20):
        cap = open_camera(device)
        if cap.isOpened():
            return cap
        cap.release()
        time.sleep(0.1)
    return cap


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
                SETTINGS[key] = round(max(lo, min(hi, SETTINGS[key] + delta)), 1)
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
    ap.add_argument("--runs", default=None,
                    help="directory to write one run log per session into "
                         "(default ../runs). Every run leaves frames.csv, "
                         "meta.json and summary.txt; the plot command is "
                         "printed at the end rather than run, so a machine "
                         "without matplotlib still records everything.")
    ap.add_argument("--no-log", action="store_true",
                    help="do not record this run at all")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="stop after N frames and print a summary; 0 = run "
                         "until q")
    return ap


def main():
    global auto_sync, cal_request, last_sent, show_settings, sink, settle_frames, SETTLE_TOL, use_filter
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
    # The sink's own deadband stops being the jitter defence and becomes a
    # backstop: hand_filter gates per axis at a finer threshold than the
    # sink's 12 counts, and leaving the coarser one in front of it would
    # swallow releases the filter had already decided were real.
    if args.deadband is None:
        sink.deadband = 1
    filt = hand_filter.HandFilter.for_camera(deg=SETTINGS["deadband_deg"])

    runlog = None
    if not args.no_log:
        root = args.runs or os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "runs"))
        try:
            runlog = run_log.RunLog.open(
                root, gains=hand_filter.camera_gains(),
                filter_params={"mincutoff": hand_filter.MINCUTOFF,
                               "beta": hand_filter.BETA,
                               "dcutoff": hand_filter.DCUTOFF,
                               "deg": SETTINGS["deadband_deg"],
                               "dt_max": hand_filter.DT_MAX},
                extra={"sink": sink.name, "device": SETTINGS["device"],
                       "calibration": hm.ACTIVE_PROFILE or "(module defaults)",
                       "rate_hz": args.rate})
            print(f"run log: {runlog.path}")
        except Exception as e:                              # noqa: BLE001
            # A run that cannot be recorded is still a run worth flying.
            print(f"not recording this run ({e})", file=sys.stderr)

    settle_frames = (args.settle_frames if args.settle_frames is not None
                     else hand_sink.settle_frames_default(sink.name))
    print(f"filter: one-euro fc={hand_filter.MINCUTOFF} beta={hand_filter.BETA}"
          f" dcutoff={hand_filter.DCUTOFF}, deadband={SETTINGS['deadband_deg']}"
          f" deg, dt clamped at {hand_filter.DT_MAX*1000:.0f} ms")
    print(f"sink={sink.name}  settle_frames={settle_frames}  "
          f"backstop deadband={sink.deadband}"
          + (f"  rate={args.rate:g} Hz" if sink.name == "daemon" else ""))

    def open_camera(device):
        c = cv2.VideoCapture(device)
        c.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        return c

    SETTINGS["device"] = args.device if args.device != 4 else SETTINGS["device"]
    try:
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        cap = open_camera(SETTINGS["device"])
        opened_device = SETTINGS["device"]
        cam_try = time.time()
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

        while not stop:
            if cal_request:
                # serviced here, not in the click handler: this is the scope
                # that holds the camera, and handing it over is the whole job
                cal_request = False
                cap = run_calibration(SETTINGS["device"], open_camera, cap)
                opened_device, ema = SETTINGS["device"], None
            if SETTINGS["device"] != opened_device:     # follow the settings plate
                cap.release()
                cap = open_camera(SETTINGS["device"])
                opened_device, ema = SETTINGS["device"], None
                filt.reset()      # new source, not a dropped frame
            ok, frame = cap.read()
            if not ok:
                # a dead camera must not take the panel with it: the hand-side
                # buttons keep working over a placeholder while we retry behind
                if time.time() - cam_try > CAM_RETRY:
                    cam_try = time.time()
                    cap.release()
                    cap = open_camera(SETTINGS["device"])
                frame = np.zeros((args.height, args.width, 3), np.uint8)
                res = None
                time.sleep(0.03)
            else:
                cam_try = time.time()
                stamps.frame()          # the latency ruler starts at the frame
                frames += 1
                frame = cv2.flip(frame, 1)
                res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            tgt = None
            raw_log = None
            feats_log = None

            trust, why = True, ""
            if res and res.multi_hand_landmarks and res.multi_hand_world_landmarks:
                draw.draw_landmarks(frame, res.multi_hand_landmarks[0],
                                    mp.solutions.hands.HAND_CONNECTIONS,
                                    styles.get_default_hand_landmarks_style(),
                                    styles.get_default_hand_connections_style())
                label = res.multi_handedness[0].classification[0]
                trust, why = hm.thumb_trust(res.multi_hand_landmarks[0].landmark,
                                            label.label, label.score)
                world = res.multi_hand_world_landmarks[0].landmark
                raw = hm.pose_from_world_landmarks(world)
                raw_log = list(raw)     # before HOLD is written into it
                if runlog is not None:
                    # The input angles as well as the targets they became.
                    # A calibration saved between a run and its analysis
                    # re-scales every target - that happened on 2026-08-07 -
                    # and these are what let an old run be re-mapped under
                    # new windows instead of only re-read under its own.
                    tf = hm.thumb_features(world)
                    feats_log = {f"curl_{f}": hm.finger_curl(
                                     world, hm.FINGER_CHAINS[f])
                                 for f in hand_filter.FINGERS}
                    feats_log["thumb_flexion"] = tf["flexion"]
                    feats_log["opposition"] = tf["opposition"]
                if not trust:
                    # MediaPipe is guessing at the thumb. Hand the filter a
                    # HOLD rather than a substitute value: a hold that is fed
                    # back in as data decays into the estimate and quietly
                    # becomes a command of its own.
                    raw[4] = raw[5] = hand_filter.HOLD
                filt.set_deadband_deg(SETTINGS["deadband_deg"])
                ema = filt.update(raw, time.time())
                # How far the filter still is from the raw signal, which is
                # what "settling" meant when this was an EMA. Held axes are
                # excluded: a hold names no raw value to be far from.
                moved = max([abs(a - b) for a, b in zip(ema or [], raw)
                             if b != hand_filter.HOLD] or [0])
                quiet = quiet + 1 if moved < SETTLE_TOL else 0
                if ema is None:
                    # an axis has never been seen - the filter has no pose to
                    # give yet, and the UI already knows what None means
                    tgt = None
                elif use_filter:
                    tgt = ema[:]
                else:
                    # HOLD is the filter's vocabulary; the old path substituted
                    # the last smoothed value, so reproduce that to compare.
                    tgt = [ema[i] if raw[i] == hand_filter.HOLD else raw[i]
                           for i in range(len(raw))]
                seen += 1
                stamps.mapped()     # targets are ready; the rest is the daemon's
            else:
                quiet = 0

            busy = sink.busy
            ui.draw_gauge(frame, tgt, busy, sink.actual)
            ui.draw_button(frame, ui.SYNC_BTN, auto_sync,
                           "SYNC ON" if auto_sync else "SYNC OFF")
            ui.draw_button(frame, ui.CAL_BTN, False, "CALIBRATE", ui.VIOLET,
                           enabled=not busy)
            ui.draw_button(frame, ui.PARK_BTN, False, "OPEN HAND", enabled=not busy)
            ui.draw_button(frame, ui.SET_BTN, show_settings, "SETTINGS", ui.VIOLET)
            if show_settings:
                ui.draw_settings(frame, SETTINGS)
            elapsed = sink.elapsed()
            if busy:
                headline, hint, tone = "Hand moving", "mirroring the pose you held", ui.VIOLET
            elif not ok:
                headline, hint, tone = ("Camera offline",
                                        f"retrying device {SETTINGS['device']} - "
                                        "pick another in SETTINGS", ui.ROSE)
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
            if tgt and not trust:
                hint += f"   |  thumb held: {why}"
            now = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - fps_t, 1e-3))
            fps_t = now
            progress = 1.0 if settle_frames == 0 else min(1.0, quiet / settle_frames)
            ui.draw_rail(frame, headline, hint, tone, progress,
                         elapsed, cal_note or sink.last_result, fps,
                         "space send   f  filter " + ("ON" if use_filter else "OFF") + "   q quit")

            # Whether this pose is worth sending is hand_filter's decision
            # now, taken per axis. What used to be here was a max over all
            # six, so one noisy axis released the whole vector - measured on
            # 2026-08-07, thumb_rot alone did that on 64% of frames and took
            # the four fingers with it, which is the twitch the operator sees.
            # The settle gate is still ANDed in for the hand_set sink, where
            # a pose costs seconds; it defaults off when streaming.
            if not tgt or last_sent is None:
                worth = bool(tgt)          # the first pose always goes
            elif use_filter:
                worth = filt.changed       # decided per axis, inside the filter
            else:
                worth = (max(abs(a - b) for a, b in zip(tgt, last_sent))
                         > BYPASS_DEADBAND)
            just_sent = False
            if tgt and auto_sync and quiet >= settle_frames and not sink.busy \
                    and worth:
                last_sent = tgt[:]
                send_pose(tgt, stamps)
                just_sent = True

            if runlog is not None:
                tele = None
                if sink.name == "daemon":
                    try:
                        st = sink._client.state()
                        if st.get("bus") == "up":
                            # `applying` is handd's own stuck detector. Without
                            # it a run where the slave stopped consuming process
                            # data looks exactly like a mechanism that absorbed
                            # every command, and the summary would report 100%
                            # absorbed as though that were about the hand.
                            tele = {"ang": st["ang"], "cur": st["cur"],
                                    "applying": st.get("applying")}
                    except Exception:                       # noqa: BLE001
                        pass    # telemetry is a bonus; never lose a frame for it
                # `ema` is the filter's own output, which is what the replay
                # check has to be able to reproduce. last_sent is not: it only
                # advances on frames teleop actually transmitted, so with AUTO
                # off - or before it is switched on - the filter's staircase
                # walks away from it and never comes back. `was_sent` carries
                # the transmitted/not distinction instead, and what went out is
                # still recoverable as the value on the frames where it is 1.
                runlog.frame(t=time.time(), seen=raw_log is not None,
                             raw=raw_log, sent=ema if raw_log is not None else None,
                             was_sent=just_sent,
                             mode="on" if use_filter else "off",
                             trust=trust, why=why, feats=feats_log, tele=tele)

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
            elif k == ord("f"):
                use_filter = not use_filter
                last_sent = None
            elif k == ord("c"):
                toggle_calibration()
            elif k == ord("o"):
                send_pose(PARK)
            elif k == ord("s"):
                show_settings = not show_settings

    finally:
        # The camera and the sink are released here rather than after the
        # loop, so an exception leaves /dev/video0 free for the next run
        # instead of held by a process that is already gone.
        cap.release()
        sink.close()
        if runlog is not None:
            try:
                path = runlog.close()
                print(f"\nrun log written to {path}")
                print(open(os.path.join(path, "summary.txt")).read(), end="")
            except Exception as e:                          # noqa: BLE001
                print(f"run log incomplete ({e}) - frames.csv is still there",
                      file=sys.stderr)
    if not args.headless:
        cv2.destroyAllWindows()
    dt = max(time.time() - t_start, 1e-6)
    print(f"{frames} frames in {dt:.1f}s = {frames / dt:.1f} FPS; "
          f"a hand was in {seen} of them ({100.0 * seen / max(frames, 1):.0f}%); "
          f"{sink.sent} pose(s) reached the {sink.name} sink")
    return 0


if __name__ == "__main__":
    sys.exit(main())
