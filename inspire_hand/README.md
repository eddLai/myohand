# inspire_hand — RH56F1-E4R-T1 EtherCAT Control API

Standalone control stack for the Inspire RH56F1 dexterous hand on `.28`
(hand plugged into the built-in RJ45, controlled over EtherCAT). The
reverse-engineered protocol notes and the full bring-up log live in the
ExoPulse_docs vault (`Inspire_RH56F1_Hand_Bringup_Ops_Log`).

## Layers

| File | Role |
|---|---|
| `handd.c` → `handd` | **Resident master.** Holds OPERATIONAL, runs the PDO loop, owns the safety layer, serves a unix socket. Execution trigger is a swappable strategy |
| `hand_client.py` | Python client for the daemon. Also `python3 hand_client.py state` |
| `hand_sink.py` | Where teleop's poses go: daemon (streaming), hand_set (per-pose), or nowhere |
| `hand_scale.py` | The one Python copy of the target scale; checks itself against `hand_ctl scale` |
| `hand_latency.py` | Client-side stamps for the latency ruler, and a reader for the CSV |
| `hand_ctl.c` → `hand_ctl` | C core (SOEM): wake → pose → disconnect-execute; JSON telemetry; setcap, no sudo |
| `hand_api.py` | Python lib + CLI. Gestures: open / fist / middle / point / release |
| `hand_server.py` | HTTP JSON API bound to `127.0.0.1:8100` only (SSH tunnel in) |
| `soem_build/hand_set.c` → `hand_set` | Lean pose setter (~2–3 s per pose); the path that predates the daemon |
| `teleop_app.py` + `run_teleop.sh` | MediaPipe webcam gesture mirroring with a SYNC button UI |
| `systemd/` | Unit + installer for running the daemon at boot (installs, never enables) |
| `experiments/` | Serial + EtherCAT bring-up probes (protocol archaeology), `rt_check.sh` |

## The daemon

    ./handd --iface=eth1                     # disconnect trigger (default)
    ./handd --iface=eth1 --trigger=sync0     # distributed clocks - unproven
    ./handd --simulate                       # no bus, for working on clients
    python3 hand_client.py state

One process holds OPERATIONAL instead of paying for it per pose, and the
safety layer lives inside it, so teleop, an EMG classifier, the HTTP
server and an ad-hoc script all reach the hand through one socket and
none of them can route around the guard.

**What makes the firmware execute is still an open question**, so it is a
strategy rather than an assumption:

| `--trigger` | What it does | Status |
|---|---|---|
| `disconnect` | Write, hold, drop the link so the SM watchdog fires, reconnect | **Default. The only path ever observed to drive this hand. Do not delete it.** |
| `sync0` | Arm distributed clocks; the slave applies its own buffer on the Sync0 interrupt | Written but unproven — DC cannot cross an ethernet switch, so it needs the direct link first |

An unrecognised `--trigger` is an error, never a silent fall back to the
default: asking for one strategy and measuring another is worse than not
running at all. `handd --explain-al=0x002d` reads an AL status code aloud,
and the `dc` command reports clock health live.

### Latency

Eight stages, the same columns for either trigger, so the two can be
compared on one ruler:

    ./handd --iface=eth1 --latency-log=/tmp/handd.csv
    python3 hand_latency.py /tmp/handd.csv

`vision` and `send` come from the client's own timestamps, `ipc` / `queue`
/ `wire` / `exec` / `move` from the daemon, `total` spans the lot. `stats`
on the socket reports the same live, plus how late each wake-up was.

### Determinism

The board is not PREEMPT_RT, so: `--cpu=3 --rt-prio=80 --lock-memory`.
Each says in its own log line whether it took effect — do not read jitter
from a run that printed a WARNING. `./experiments/rt_check.sh` reports
what else is in the way. Measured on the KD240 at 1 kHz, worst-case
wake-up lateness under six busy loops: **3052 µs untuned, 121 µs tuned**.

## Quick use

    ./hand_ctl state                      # telemetry, no motion
    ./hand_ctl scale                      # what the target numbers mean, no bus
    ./handd --iface=eth1 &                # resident master
    python3 hand_client.py state          # talk to it
    python3 hand_api.py open              # gestures from CLI (per-pose path)
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

The window itself is an instrument panel (`teleop_ui.py`), built for an
operator whose eyes are on their own hand: one large line says what to do next
("Hold still", "Ready", "Hand moving"), and a schematic of the right hand shows
the commanded posture, thumb included - that thumb swings with the rotation
axis, so the gauge reads as a hand rather than as a bar chart. Amber means
ready, violet means the hand is executing.

Four controls sit above the video: SYNC mirrors your hand automatically,
CALIBRATE records your range, OPEN HAND sends every joint open, and SETTINGS
opens a plate for grip force, speed, camera and smoothing - stepped rather than
dragged, saved to teleop_settings.json, applied to the next pose. The gauge
draws two readings: the amber fill is the pose that was asked for, the pale tick
is where the hand reported it got to. They separate whenever a guard clamps or
an axis stalls.

Where the poses go is `--sink`:

| `--sink` | Behaviour |
|---|---|
| `daemon` (default) | Stream the latest pose into `handd` at `--rate` Hz. Continuous following |
| `hand_set` | One subprocess per pose. Slow by construction, and the only path measured to drive the hand |
| `none` | Dry run. Camera, mapping and window with no hand and no daemon |

The settle gate — wait for five frames within 120 units before sending —
existed only because a pose cost two to three seconds. It is
`--settle-frames` now and defaults to **off** for the daemon sink, because
waiting for the operator to hold still is the opposite of following them.
`--headless --max-frames=N` runs the whole chain over SSH with no display.

### Calibration profiles

`calibration.json` holds **named profiles** and an `active` pointer, because
those windows are measured data and the CALIBRATE button used to overwrite
them. A save always lands under a new name and makes it active; the old one
stays. `python3 hand_mapping.py` lists them, `--profile NAME` picks one.

## Axis order and semantics (F1, reverse-engineered)

Order: `[pinky, ring, middle, index, thumb_bend, thumb_rot]`.
Targets: `-1` = leave unchanged. The rest is **not** `0..2000`, and the
code has not caught up yet — `hand_ctl scale` still reports the old scale.
Measured three ways on 2026-08-06: parking `1100` gave `ANGLEACT` `1101`,
`1272` gave `1274`, and commanding `1509` landed on `1508`. **Targets are
ANGLEACT counts, one for one, roughly `890` closed to `1850` open.** A
target below ~`890` drives into the closed stop instead of to the number.
So `hs_ang_to_target` is a spurious conversion and the `0..2000` clamp
admits values the mechanism cannot reach. Fixing it reaches into
`hand_mapping.py` and `teleop_app.py`, so it is filed, not done.

The hand applies a pose continuously, every cycle, exactly as an
SM-Synchron slave should — **but only if process data arrives no faster
than about 500 Hz**. At 1 kHz it applies nothing at all.

That was measured on 2026-08-06 with the link up, OPERATIONAL held, and
every period far below the 99.9 ms watchdog, so no timeout was involved
in any of it:

| PDO period | travel | current | cycle-exceeded counter |
|---|---|---|---|
| 1 ms | 0 | 0 mA | **+2244 in 4 s** |
| 2 ms | 181 | 62 mA | 0 |
| 3-8 ms | ~180 | 56-71 mA | 0 |

The slave's application needs more than a millisecond per cycle, and
SM-Synchron starts a new cycle on every arriving frame, so a 1 kHz feed
interrupts it before it can ever finish and the outputs are never copied.
It says so itself in `0x1C32:12`, its cycle-exceeded counter, which no run
read until that day. `0x1C32:05` advertises a 100 us minimum cycle; that
figure is wrong on this device by an order of magnitude.

So "the firmware executes a pose only after the master disconnects" was
never true, and neither was the watchdog theory that replaced it.
Starving the link for 100 ms worked for the same reason a slow feed does:
it stops interrupting the slave. Every layer here paid 2-3 s per pose for
a problem that was its own. Six other explanations were tested and
eliminated first (switch topology, distributed clocks, master cadence
during the transition, `ENABLE_SET`, output image size, sync mode); full
evidence in `experiments/results_2026-08-06/`, written up in the
ExoPulse_docs vault under `Execution_Trigger_Settled`.

`handd` therefore defaults to `--rate-hz=500`. Do not raise it to 1000.

Per-pose costs on the old path: `hand_ctl` ~10–20 s including the wake
wiggle and telemetry, `hand_set` ~2–3 s. Most of that is process startup
and conservative waiting rather than the protocol, which is what `handd`
removes.

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
`guarded` / `guard_note`.

**The target scale is defined once**, in `hand_safety.h` — `hs_clamp_target`,
`hs_target_valid`, `hs_ang_to_target`, `hs_target_to_ang`. Nothing else in
the C tree divides by the span or clamps against a literal 0/2000. That
discipline is what makes the pending correction a small change: the
question it was hedging against is now **settled — the command field is
ANGLEACT-style `890..1850`, not `0..2000`** (see Axis order above), so
`hs_ang_to_target` should collapse to identity and the clamp should move
to the real travel. Until that lands, every layer is consistently wrong in
the same place rather than inconsistently wrong in six. Python mirrors it
in `hand_scale.py` and verifies itself against `hand_ctl scale`.

Offline checks, none of which need hardware:

    make test && ./test_safety        # 16 interlock and scale checks
    python3 test_scale.py             # C and Python agree on the scale
    python3 test_daemon.py            # the daemon, against a simulated slave
    python3 test_teleop_sink.py       # the streaming client path
    python3 test_calibration.py       # profiles cannot be clobbered
    python3 test_mapping.py           # the mapping is viewpoint-invariant
    python3 test_ui_render.py         # the panel draws, with no display

## Build and setup (from a clean clone)

    ./setup.sh                            # one-shot: venv, clones, cmake, make, cap

⚠️ **Not on the KD240** — `setup.sh` refuses to run on aarch64. The board has
1.9 GB of RAM and no swap, and pip building a vision wheel from source OOMs
it. There, build the C side only:

    export PATH="$HOME/rh56f1_kd240/ethercat/buildenv/bin:$PATH"   # cmake >= 3.28
    make all && make cap

Or step by step:

    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    git clone https://github.com/OpenEtherCATsociety/SOEM.git soem_build/SOEM
    git clone https://github.com/Kazuhito00/hand-gesture-recognition-using-mediapipe.git
    cmake -S soem_build/SOEM -B soem_build/build
    cmake --build soem_build/build -j4
    make all && make cap                  # cap needs sudo once per rebuild

## Known limits

The 24V/3A PSU handles gestures but sits under the hand's 5 A peak-grip
spec. Thumb force-sensor calibration awaits vendor F1 documentation.

`fist` does not reach a grip: the four fingers only travel from ANGLEACT
~1790 to ~1730 rather than into the closed band below 1000. The same
symptom was solved once before by raising force and speed to 1000, and
`hand_api.pose()` defaults to 500/800 — but making an unattended hand
squeeze harder needs somebody standing next to it, so it is untested.

MediaPipe pins XNNPACK to a single thread and will not let you change it,
so the teleop chain runs at **3.9 FPS on the board** when it is
re-detecting (measured 2026-08-06, 320×240, no hand in frame). Tracking is
faster. Getting past that means building the palm→landmark pipeline
directly on `ai-edge-litert`, where the thread count is yours.
