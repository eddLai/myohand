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

## Axis order and semantics (F1, reverse-engineered)

Order: `[pinky, ring, middle, index, thumb_bend, thumb_rot]`.
Targets: `0` = closed, `2000` = open, `-1` = leave unchanged.

The firmware executes a pose only AFTER the master disconnects, so each
pose call ends by closing the session: `hand_ctl` takes ~10–20 s (wake
wiggle and telemetry included) and `hand_set` takes ~2–3 s. Teleop
therefore updates at roughly one pose every 2–3 s until vendor docs
reveal the realtime execution trigger.

## Safety

`hand_ctl` enforces:

- collision guard: rejects `index<600 AND thumb_bend<600` together
  (mechanical clash, trips STA=5); `fist()`/`middle_finger()` stagger phases
- telemetry reads write `-1` targets only (never zeros: `0` = fist!)
- `force<=1000` (default 500), `speed 50..1000` (default 800)

`teleop_app.py` adds layered guards (fist-like poses force
`thumb>=1400`, palm-ward thumb rotation requires an open index) and EMA
smoothing. `hand_set` itself bypasses the `hand_ctl` guards: the shared
driver-level interlock is tracked as a P1 TODO in the vault ops log.

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
