# hand_fw — RH56F1-E4R-T1 EtherCAT Control API

Control stack for the Inspire RH56F1 dexterous hand on `.28` (hand
plugged into the built-in RJ45, controlled over EtherCAT). The
reverse-engineered protocol notes and the full bring-up log live in the
ExoPulse_docs vault (`Inspire_RH56F1_Hand_Bringup_Ops_Log`). The vision
side lives in `../camera/`, the teleop app in `../teleop/`; this folder
is everything that talks to the hand itself.

## Layers

| File | Role |
|---|---|
| `hand_ctl.c` → `hand_ctl` | C core (SOEM): wake → pose → disconnect-execute; JSON telemetry; setcap, no sudo |
| `hand_api.py` | Python lib + CLI. Gestures: open / fist / middle / point / release |
| `hand_server.py` | HTTP JSON API bound to `127.0.0.1:8100` only (SSH tunnel in) |
| `soem_build/hand_set.c` → `hand_set` | Lean pose setter (~2–3 s per pose); teleop sends through this path |
| `../teleop/teleop_app.py` + `run_teleop.sh` | MediaPipe webcam gesture mirroring with a SYNC button UI |
| `../camera/hand_mapping.py` | Skeleton → joint targets; the mapping the teleop and the NN labels share |
| `experiments/` | Serial + EtherCAT bring-up probes (protocol archaeology) |
| `hand_pid.py` | Slow outer-loop trim: per-shot integral correction from ANGLEACT readback (pure corrector, wire into your own send path) |
| `test_pid.py` | Offline tests for hand_pid, no hardware: `python3 test_pid.py` |
| `geometry/` | Offline STEP pipeline that generates `hand_collision_table.h` |
| `hand_collision_table.h` | GENERATED thumb-vs-finger minimum-target tables (do not edit) |

## Quick use

    ./hand_ctl state                      # telemetry, no motion
    ../venv/bin/python3 hand_api.py open  # gestures from CLI
    ../teleop/run_teleop.sh               # webcam teleop (SYNC button; SPACE/A/Q keys)
    ../venv/bin/python3 hand_server.py &  # REST for other projects:
    #   ssh -L 8100:127.0.0.1:8100 eddlai@120.126.83.28
    #   curl -X POST http://127.0.0.1:8100/gesture/open

## Gesture teleop

`../teleop/run_teleop.sh` opens the webcam window with a SYNC button;
`../camera/hand_mapping.py` turns the skeleton into targets. (The notes
below document that pipeline; the code lives in those folders.)

Flexion is scored as **joint angles on MediaPipe's world landmarks**, not as
distance ratios over the projected image. Angles between bones do not change
when the hand rotates in front of the camera, so the same fist reports the same
targets from any viewpoint. `test_mapping.py` views a synthetic hand from 45
orientations and measures the wander: 0 target units for the joint-angle
mapping, up to 1700 (the entire travel) for the distance-ratio one it replaced.

The thumb is decomposed in a palm-fixed frame rather than read as one
scalar. `thumb_rot` is driven by the **opposition angle** alone — the
metacarpal's rotation about the wrist→index-MCP axis, `atan2` of its
palm-normal vs in-plane components — which is invariant to thumb flexion
and to splay toward the index. This replaced an unsigned palm-plane
elevation that mixed opposition, abduction and CMC flexion into one
number, so the rot axis twitched whenever the thumb merely bent.
`thumb_features()` exposes the three separated channels
`{flexion, abduction, opposition}` in degrees for downstream learners
(the EMG→pose network trains against these labels); abduction has no
robot axis but is reported by `calibrate.py` as "thumb splay".
`test_mapping.py` proves the channels do not leak into each other and
that a mirrored left hand (`handedness="Left"`) maps identically.

The decomposition is only as good as the landmarks, and MediaPipe draws
plausible thumbs it cannot see, so a **trust gate** (`thumb_trust()`)
decides per frame whether the thumb channels can be believed rather than
trying to fix them: (1) the handedness label disagreeing with the hand
locked at calibration (`HANDEDNESS` in calibration.json, majority-voted
while the operator demonstrates their range) means the net currently
perceives the mirror hand, whose inverted depth relief flips the
opposition sign; (2) a label score under `LABEL_SURE` means it is
mid-flip; (3) `hand_facing()` reads palm/back from the 2D silhouette's
signed area — the part MediaPipe gets right even when its depth is
hallucinated — and refuses the edge-on band where facing is undefined;
(4) `thumb_occluded()` flags a thumb drawn inside the palm outline while
the back of the hand faces the camera. Untrusted frames hold the two
thumb axes at the last believed pose and are excluded from calibration
windows; the rail shows why ("thumb held: edge-on / hand looks flipped /
thumb hidden"). Fingers are never gated - they stay visible from either
side.

The window itself is an instrument panel (`teleop_ui.py`), built for an
operator whose eyes are on their own hand: one large line says what to do next
("Hold still", "Ready", "Hand moving"), and a schematic of the right hand shows
the commanded posture, thumb included - that thumb swings with the rotation
axis, so the gauge reads as a hand rather than as a bar chart. Amber means
ready, violet means the hand is executing.

Five controls sit above the video: SYNC mirrors your hand automatically,
CALIBRATE records your range, OPEN HAND sends every joint open, SETTINGS
opens a plate for grip force, speed, camera and smoothing - stepped rather than
dragged, saved to teleop_settings.json, applied to the next pose - and HAND
probes the EtherCAT link without motion (`hand_set -1 ...` is a hold pose).

Camera and hand come up independently: the window opens even if either is
missing. A dead camera leaves the panel on a placeholder, retries in the
background and can be switched from SETTINGS; a dead hand flips the HAND
button to OFFLINE and switches AUTO sync off while tracking and calibration
keep running. Either side recovers from its own control without a restart. The gauge
draws two readings: the amber fill is the pose that was asked for, the pale tick
is where the hand reported it got to. They separate whenever a guard clamps or
an axis stalls.

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

    ../setup.sh                           # one-shot at repo root: venv, SOEM, cmake, make, cap

Or step by step (from the repo root):

    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    git clone https://github.com/OpenEtherCATsociety/SOEM.git hand_fw/soem_build/SOEM
    cmake -S hand_fw/soem_build/SOEM -B hand_fw/soem_build/build
    cmake --build hand_fw/soem_build/build -j4
    make -C hand_fw all && make -C hand_fw cap   # cap needs sudo once per rebuild;
                                                 # hand_set.c finds hand_safety.h via -I .,
                                                 # so always build with make -C hand_fw

## Known limits

The 24V/3A PSU handles gestures but sits under the hand's 5 A peak-grip
spec. Realtime streaming control and thumb force-sensor calibration
await vendor F1 documentation.


## Closed-loop trim (hand_pid.py)

The firmware executes a pose only after the master disconnects (2-3 s a
shot, no feedback while it runs), so a realtime PID is impossible; what
works is a per-shot integral trim. `HandPID.correct(req, ang_act)`
returns the biased targets for the next shot - held (-1) axes pass
through untouched, the integrator freezes in the deadband / on rails /
on STA 5-6-7, and resets when the target jumps. The corrected targets
still go through hand_safety inside hand_ctl/hand_set, so the trim can
never bypass the interlock. Offline tests: `python3 test_pid.py`
(convergence needs at most 3 corrected shots over a +-10% gain and
+-80-unit offset plant).

## Geometry collision tables (hand_collision_table.h)

Generated from the vendor right-hand STEP model (geometry/, see
geometry/README.md). The tables agree with the empirical interlock
about the f>=600 world (no restriction) and are stricter in the
half-curled low-rotation pocket the scalar rules miss (thumb_bend
floors up to 1250). The scalar rules in hand_safety.c REMAIN as the
floor: the model was calibrated on 15 anchor poses with millimetre
margins, so only an on-hand boundary replay (conservative force/speed,
poses stepped just outside the table boundary) may retire them.
Regenerate after any calibration change:

    geometry/venv-geo/bin/python geometry/sample_collisions.py         geometry/links.yaml <mesh_dir> <cylinders.json> <grid.npz>
    geometry/venv-geo/bin/python geometry/build_tables.py         <grid.npz> geometry/links.yaml hand_collision_table.h

build_tables refuses to write the header unless the anchor gates pass.
