# inspire_hand — RH56F1-E4R-T1 EtherCAT Control API

Standalone control stack for the Inspire RH56F1 dexterous hand on `.28`
(hand plugged into the built-in RJ45, controlled over EtherCAT). The
reverse-engineered protocol notes and the full bring-up log live in the
ExoPulse_docs vault (`Inspire_RH56F1_Hand_Bringup_Ops_Log`).

## Layers

| File | Role |
|---|---|
| `hand_ctl.c` → `hand_ctl` | C core (SOEM): wake → pose → disconnect-execute; JSON telemetry; setcap, no sudo |
| `hand_api.py` | Python lib + CLI. Gestures: open / fist / middle / point / release |
| `hand_server.py` | HTTP JSON API bound to `127.0.0.1:8100` only (SSH tunnel in) |
| `soem_build/hand_set.c` → `hand_set` | Lean pose setter (~2–3 s per pose); teleop sends through this path |
| `teleop_app.py` + `run_teleop.sh` | MediaPipe webcam gesture mirroring with a SYNC button UI |
| `experiments/` | Serial + EtherCAT bring-up probes (protocol archaeology) |

## Quick use

    ./hand_ctl state                      # telemetry, no motion
    python3 hand_api.py open              # gestures from CLI
    ./run_teleop.sh                       # webcam teleop (SYNC button; SPACE/A/Q keys)
    python3 hand_server.py &              # REST for other projects:
    #   ssh -L 8100:127.0.0.1:8100 eddlai@120.126.83.28
    #   curl -X POST http://127.0.0.1:8100/gesture/open

## Gesture teleop

`run_teleop.sh` opens the webcam window with a SYNC button; `hand_mapping.py`
turns the skeleton into targets.

Flexion is scored as **joint angles on MediaPipe's world landmarks**, not as
distance ratios over the projected image. Angles between bones do not change
when the hand rotates in front of the camera, so the same fist reports the same
targets from any viewpoint. `test_mapping.py` views a synthetic hand from 45
orientations and measures the wander: 0 target units for the joint-angle
mapping, up to 1700 (the entire travel) for the distance-ratio one it replaced.

Because a pose costs the hand two to three seconds, AUTO sync waits for the
pose to **settle** - five consecutive frames within 120 units - before sending,
so the hand mirrors what the operator meant to hold rather than a posture that
merely passed by. The window shows MOVING / SETTLED / SENDING accordingly.

## Axis order and semantics (F1, reverse-engineered)

Order: `[pinky, ring, middle, index, thumb_bend, thumb_rot]`.
Targets: `0` = closed, `2000` = open, `-1` = leave unchanged.

The firmware executes a pose only AFTER the master disconnects, so each
pose call ends by closing the session: `hand_ctl` takes ~10–20 s (wake
wiggle and telemetry included) and `hand_set` takes ~2–3 s. Teleop
therefore updates at roughly one pose every 2–3 s until vendor docs
reveal the realtime execution trigger.

## Safety

Every binary routes its targets through the shared driver layer
(`hand_safety.c`) before they reach the PDO, so no caller — Python API,
HTTP server, teleop, or an ad-hoc script — can command a pose that jams
the mechanism:

- **joint interlock**: a pose closing index and thumb together lifts the
  thumb clear (they collide mechanically and trip STA=5); palm-ward thumb
  rotation is refused while the index is curled. Axes left unchanged
  (`-1`) are judged by live ANGLEACT, not by the absent command.
- **stall relief**: an axis found in STA 5/6 or drawing over 400 mA from a
  previous execution is backed off toward open before anything else is
  written (a stall observed at 1.1 A / 58 C motivated this).
- **per-axis profile**: thumb-bend gets a force limit above its
  1300-1857 g phantom reading, plus a lower speed to offset the headroom.
- **bus lock**: `flock` serializes masters, since two on one NIC make the
  slave refuse OPERATIONAL.
- range clamp 0..2000, `force<=1000` (default 500), `speed 50..1000`.

Guards clamp rather than reject, so a streaming teleop source degrades to
a safe pose instead of failing. `hand_ctl` reports what it changed in
`guarded` / `guard_note`. Offline checks: `make test && ./test_safety`.

## Build and setup (from a clean clone)

    ./setup.sh                            # one-shot: venv, clones, cmake, make, cap

Or step by step:

    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    git clone https://github.com/OpenEtherCATsociety/SOEM.git soem_build/SOEM
    git clone https://github.com/Kazuhito00/hand-gesture-recognition-using-mediapipe.git
    cmake -S soem_build/SOEM -B soem_build/build
    cmake --build soem_build/build -j4
    make all && make cap                  # cap needs sudo once per rebuild

## Known limits

The 24V/3A PSU handles gestures but sits under the hand's 5 A peak-grip
spec. Realtime streaming control and thumb force-sensor calibration
await vendor F1 documentation.
