# hand_fw — RH56F1-E4R-T1 EtherCAT Control API

Standalone control stack for the Inspire RH56F1 dexterous hand over
EtherCAT. It has been driven from three hosts and the NIC is different on
each, so nothing here hardcodes one — `$ECAT_IFACE` picks it and
`experiments/ecat_scan` says which one answers. The 2026-08-06 results
were taken on the KD240 (`.228`) with the hand cabled directly into
`eth1`, one of the PL-backed J25 ports rather than the board's built-in
RJ45. The reverse-engineered protocol notes and the full bring-up log
live in the ExoPulse_docs vault (`Inspire_RH56F1_Hand_Bringup_Ops_Log`).

## Layers

| File | Role |
|---|---|
| `handd.c` → `handd` | **Resident master.** Holds OPERATIONAL, runs the PDO loop, owns the safety layer, serves a unix socket. Execution trigger is a swappable strategy |
| `hand_client.py` | Python client for the daemon. Also `python3 hand_client.py state` |
| `hand_sink.py` | Where teleop's poses go: daemon (streaming), hand_set (per-pose), or nowhere |
| `hand_scale.py` | The one Python copy of the target scale; checks itself against `hand_ctl scale` |
| `hand_latency.py` | Client-side stamps for the latency ruler, and a reader for the CSV |
| `hand_ctl.c` → `hand_ctl` | C core (SOEM): wake → pose → exit; JSON telemetry; setcap, no sudo. Cycles at 500 Hz, so the pose executes during the hold and the telemetry it prints is the pose that happened, not the one about to |
| `hand_api.py` | Python lib + CLI. Gestures: open / fist / middle / point / release |
| `hand_server.py` | HTTP JSON API bound to `127.0.0.1:8100` only (SSH tunnel in) |
| `soem_build/hand_set.c` → `hand_set` | Lean pose setter (~2–3 s per pose); the path that predates the daemon |
| `../teleop/teleop_app.py` + `../teleop/run_teleop.sh` | MediaPipe webcam gesture mirroring with a SYNC button UI |
| `systemd/` | Unit + installer for running the daemon at boot (installs, never enables) |
| `experiments/` | Serial + EtherCAT bring-up probes (protocol archaeology), `rt_check.sh`, and the 2026-08-06 instruments: `ecat_scan`, `ecat_interrogate`, `sii_dump`, `coe_startup`, `dc_check`, `compliant_op`, `op_execute_hunt`, `watchdog_trigger`, `wd_pace`, `syncmode_test`, `rate_sweep`, `ecat_persistent_probe`. `make probe && sudo make cap-probe` builds them; raw output in `results_2026-08-06/` |
| `geometry/` | Offline STEP pipeline that generates `hand_collision_table.h` |
| `hand_collision_table.h` | GENERATED thumb-vs-finger minimum-target tables (do not edit) |

## The daemon

    ./handd --iface=eth1                     # continuous, 500 Hz (default)
    ./handd --iface=eth1 --trigger=watchdog  # fallback: silence applies it
    ./handd --simulate                       # no bus, for working on clients
    python3 hand_client.py state

One process holds OPERATIONAL instead of paying for it per pose, and the
safety layer lives inside it, so teleop, an EMG classifier, the HTTP
server and an ad-hoc script all reach the hand through one socket and
none of them can route around the guard.

**What makes the hand execute is settled** (see Axis order below): it
applies process data in OPERATIONAL like any other slave, provided the
feed is slow enough for its 18–27 ms application cycle. The trigger stays
a swappable strategy anyway, because the wrong answers are how every
result before 2026-08-06 was measured and a firmware update could bring
them back:

| `--trigger` | What it does | Status |
|---|---|---|
| `continuous` | Write the target and keep cycling | **Default.** Verified on the hand: a pose is reached in 0.4–0.5 s, current flows, the link never drops |
| `watchdog` | Write, then send nothing until the SM watchdog (99.9 ms, from `0x0400`/`0x0420`) applies it, then ACK the error and climb back to OP | Works, and needs no reconnect. A fallback for a unit that will not follow continuously |
| `disconnect` | Write, hold, drop the link, reconnect | The same silence bought at the price of a full re-enumeration. The reference path — every pre-2026-08-06 measurement went through it |
| `sync0` | Arm distributed clocks; the slave applies its own buffer on the Sync0 interrupt | **Measured not to work**, though the reason now looks like the cycle time rather than DC: Sync0 at 1 ms *is* a 1 ms feed. Kept so the negative result reproduces |

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
what else is in the way. Measured on the KD240 at 1 kHz against the
simulated slave, worst-case wake-up lateness under six busy loops:
**3052 µs untuned, 121 µs tuned**. The rate is historical — the daemon
runs at 500 Hz now, since 1 kHz is the one rate this hand applies nothing
at — and the numbers only get easier with twice the period, so they still
bound what a 2 ms loop has to survive.

## Quick use

    ./hand_ctl state                      # telemetry, no motion
    ./hand_ctl scale                      # what the target numbers mean, no bus
    ./handd --iface=eth1 &                # resident master
    python3 hand_client.py state          # talk to it
    python3 hand_api.py open              # gestures from CLI (per-pose path)
    ../teleop/run_teleop.sh                       # webcam teleop (SYNC button; SPACE/A/Q keys)
    python3 hand_server.py &              # REST for other projects:
    #   ssh -L 8100:127.0.0.1:8100 eddlai@120.126.83.28
    #   curl -X POST http://127.0.0.1:8100/gesture/open

## Manual smoke test on the KD240

Verified 2026-08-06 against `eth1` (the PL-backed J25 port; `eth0` is the
board's normal network uplink and never has the hand on it). Binaries
were already built (`make -C hand_fw all && make -C hand_fw cap`) and
the SOEM build reused from the pre-restructure checkout rather than
rebuilt on the board.

**Run every EtherCAT/`hand_ctl`/`handd` command as `ubuntu`, not root.**
`/tmp/inspire_hand.bus.lock` is `ubuntu:ubuntu 0664`, and on this board
root cannot `flock()` a file it does not own even though it can open it
- `hs_lock()` then reports the generic "BUS BUSY" (indistinguishable
from an actual second master) rather than a permission error. `sudo -u
ubuntu ...` for each command, or `sudo -u ubuntu -i` once for a shell.

    ip -br link show eth1                          # want LOWER_UP; if not, check the J25 cable
    sudo -u ubuntu ./experiments/ecat_scan eth1     # read-only: confirms the slave answers
    sudo -u ubuntu env ECAT_IFACE=eth1 ./hand_ctl state   # read-only telemetry
    sudo -u ubuntu env ECAT_IFACE=eth1 ./hand_ctl scale   # confirms C and the header agree: 890..1850

    # start the daemon - wakes STA=7 axes (a small in-place wiggle) and
    # holds OPERATIONAL, but sends no pose until one is asked for
    sudo -u ubuntu env ECAT_IFACE=eth1 ./handd --iface=eth1 &
    sudo -u ubuntu python3 hand_client.py state     # bus:up, applying:true, sta all 2

    # the actual motion test - routes through handd since it is running
    sudo -u ubuntu python3 hand_api.py open

    # release when done
    sudo -u ubuntu pkill -TERM handd                # "shutting down (the hand keeps whatever pose it was last given)"

## One API, two paths

`hand_api.InspireHand` reaches the hand through `handd` when the daemon is
up and by spawning `hand_ctl` when it is not. Method names, arguments and
the dict that comes back are identical either way, so a module that
imports this keeps working across the switch without a line changing —
that is deliberate, and `test_api_compat.py` pins it.

    hand = InspireHand()          # picks a path; hand.via says which
    hand.pose([...], force=500, speed=800)
    hand.open_hand(); hand.fist(); hand.point()

What differs is only the cost. On the daemon path a pose is a couple of
milliseconds and `settle=True` waits for the axes to actually stop rather
than sleeping a fixed time. On the `hand_ctl` path it is a fresh
connect/wake/write per call — about 1.9 s, most of it enumeration and the
axis's own travel.

Two details worth knowing if you extend it. `pose()` has always taken
`force` and `speed` per call, so `handd` grew a `profile` command rather
than let the daemon path silently drop them. And if the daemon dies
mid-session the client falls back to `hand_ctl` for that call and every
call after it, instead of raising into the caller.

`hand_client.HandClient` is still there for anything that wants the
daemon directly — streaming targets, latency stamps, `dc`, `stats`. Use
`InspireHand` when you want gestures and portability, `HandClient` when
you want the loop.

## Running the whole thing

Verified end to end on `.112` on 2026-08-06: camera to MediaPipe to
mapping to `handd` to the hand, operator waves, hand follows.

    # everything in one terminal: starts handd, runs teleop, and stops the
    # daemon again when teleop exits - Ctrl+C included
    ../teleop/run_teleop.sh --iface=enp17s0 --device=0

or the same thing in pieces, when you want the daemon to outlive the
window:

    # 1. the daemon, holding the bus
    ./handd --iface=enp17s0 &

    # 2. prove the control half before adding a camera to the picture
    python3 verify_following.py

    # 3. the vision half
    ../teleop/run_teleop.sh --device=0        # sees the daemon, leaves it running

Click **SYNC** in the window; nothing is sent until you do, and the rail
says so ("Ready - press space to send this pose"). **OPEN HAND** sends
regardless of SYNC, which makes it the fastest way to prove the chain is
alive.

## Interrupting anything

Ctrl+C releases what was held, on every entry point, and
`test_release.py` starts each one for real, interrupts it, and asks the
kernel whether the resource came back.

Worth being precise about what needs guaranteeing. File descriptors -
cameras, sockets, `flock` - the kernel reclaims when a process dies, even
on SIGKILL. What needs help is that the process **actually dies** when
asked, and the two things the kernel will not undo: a child it spawned,
and a hand left mid-motion.

| Entry point | On interrupt |
|---|---|
| `../teleop/teleop_app.py` | SIGINT/SIGTERM set a flag the loop checks; camera and sink released in a `finally` |
| `handd` | SIGINT/SIGTERM/SIGHUP; unlinks its socket, drops the bus lock, leaves the hand parked |
| `hand_server.py` | `shutdown()` lets in-flight requests finish, then `server_close()` and releases the hand |
| `verify_following.py` | parks the axis it was sweeping instead of leaving it chasing a sine |
| `HandSetSink` | `close()` stops the `hand_set` it spawned — an orphan would keep the bus lock |
| `../teleop/run_teleop.sh` | one EXIT trap stops teleop first, then the daemon, but only one it started |
| `InspireHand` | usable as `with InspireHand() as hand:` |

teleop was the one that actually broke: it handled no signals, and
`cap.release()` sat after the main loop, on the one path an interrupt
never takes. A wrapper cannot fix that from outside — an OpenCV read does
not reliably hand control back to Python in time for a handler, so the
flag has to be checked by the loop itself.

`../teleop/run_teleop.sh` only stops a daemon it started itself. If one is already
answering it says so and leaves it alone, because killing something
another window is driving would be the worse surprise. `handd` shuts down
cleanly on SIGINT, SIGTERM and SIGHUP, so Ctrl+C and closing the terminal
both end the same way - parked where it was, bus released, socket removed.
Both it and `hand_client` read `$HAND_SOCKET`, so exporting it moves the
pair together rather than splitting them onto two paths.

What step 2 measured: 599 targets at 50 Hz over 12 s, commanded swing 300
counts, axis travelled 295, best-fit lag 100 ms with 8 counts of mean
error, and the daemon's own breakdown puts **52 µs on the wire against
82 ms of travel**. The protocol is 0.06% of the response. Everything else
is the motor.

> **Never run a second master while `handd` holds the bus.** `ecat_scan`
> looks read-only and is not: its `config_init` drives the slave's state
> machine, and doing that underneath a running daemon leaves the slave
> accepting targets and applying none of them. Every indicator keeps
> saying it is fine - `bus up`, `al=0`, every reply `ok`, `seq` climbing,
> telemetry updating - with no motion and no current.
>
> Two things now guard it. `ecat_scan` takes the same bus lock every other
> tool takes and refuses rather than becoming that second master. And the
> daemon checks its own work: it already knew, per step, whether a
> commanded move produced travel, and now counts the ones that produce
> neither travel nor current. Three in a row and it says so and, by
> default, exits with code 5 rather than answering `ok` while driving
> nothing — a supervisor restarts it, which was always the only recovery.
> `--on-stuck=report` keeps it up instead, with `applying:false` in
> `state` and `hello`.
>
> To read state without a second master, ask the daemon:
> `hand_client.HandClient().state()`.

## Gesture teleop

`../teleop/run_teleop.sh` opens the webcam window with a SYNC button; `../camera/hand_mapping.py`
turns the skeleton into targets.

Flexion is scored as **joint angles on MediaPipe's world landmarks**, not as
distance ratios over the projected image. Angles between bones do not change
when the hand rotates in front of the camera, so the same fist reports the same
targets from any viewpoint. `../camera/test_mapping.py` views a synthetic hand from 45
orientations and measures the wander: 0 target units for the joint-angle
mapping, up to 1700 (the entire travel) for the distance-ratio one it replaced.

The window itself is an instrument panel (`../teleop/teleop_ui.py`), built for an
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

`../camera/calibration.json` holds **named profiles** and an `active` pointer, because
those windows are measured data and the CALIBRATE button used to overwrite
them. A save always lands under a new name and makes it active; the old one
stays. `python3 hand_mapping.py` lists them, `--profile NAME` picks one.

## Axis order and semantics (F1, reverse-engineered)

Order: `[pinky, ring, middle, index, thumb_bend, thumb_rot]`.
Targets: `-1` = leave unchanged. The rest is **not** `0..2000`. Measured
three ways on 2026-08-06: parking `1100` gave `ANGLEACT` `1101`, `1272`
gave `1274`, and commanding `1509` landed on `1508`. **Targets are
ANGLEACT counts, one for one, roughly `890` closed to `1850` open.** A
target below ~`890` drives into the closed stop instead of to the number.

The tree now says so throughout: `HS_TGT_MIN/MAX` are `890/1850`,
`hs_ang_to_target` is the identity (with a compile-time check that the two
pairs of bounds cannot drift apart again), and every constant that was a
position or a distance on the old scale was carried to the position it
named — the interlock thresholds, the stall-relief backoff, the mapping's
`T_MIN`/`ROT_MIN`, the gesture library, the teleop settle tolerance, the
UI gauge. The gauge is worth singling out: it divided by a hardcoded
`2000`, so a fully closed finger drew as 45% filled; it reads the scale
now. `test_scale.py` and `test_safety.c` assert the identity rather than
describing it, so a reintroduced conversion fails a test.

One thing was converted by arithmetic rather than re-measured, and it is
marked as such in `hand_safety.c`: the index/thumb interlock thresholds.
They preserve the physical positions the originals named, but nobody has
driven the index into the thumb again to confirm the angles.

Per-axis travel is not the same on every axis — thumb-bend rests at
`~1375` and thumb-rot at `~1048`, so commanding `1850` on those parks
them against their stop. Measuring each axis's real ends is not done.

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
never true, and neither was the watchdog theory that replaced it. Both
`hand_ctl` and `hand_set` now cycle at 500 Hz rather than 1 kHz, so a
per-pose call executes while it is still connected: `hand_ctl pose`
returns in about **1.9 s** instead of 10-20, and the JSON it prints
describes the pose that happened.
Starving the link for 100 ms worked for the same reason a slow feed does:
it stops interrupting the slave. Every layer here paid 2-3 s per pose for
a problem that was its own. Six other explanations were tested and
eliminated first (switch topology, distributed clocks, master cadence
during the transition, `ENABLE_SET`, output image size, sync mode); full
evidence in `experiments/results_2026-08-06/`, written up in the
ExoPulse_docs vault under `Execution_Trigger_Settled`.

`handd` therefore defaults to `--rate-hz=500`. Do not raise it to 1000.

Measured through `handd --trigger=continuous` on the hand: a four-finger
pose is reached in **0.4–0.5 s**, which is the mechanism's own travel and
nothing else — the link stays up, OPERATIONAL is held, current flows the
whole way. Per-pose costs on the older paths, all of them self-inflicted:
`hand_ctl` **~1.9 s** and `hand_set` ~2–3 s, nearly all of it a fresh
enumeration and wake per call. Both cycle at 500 Hz like the daemon now,
so the pose executes during the hold rather than on the way out and the
telemetry they print describes the pose that happened. What they still pay
for, and the daemon does not, is connecting at all.

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
- **geometry tables**: `hand_collision_table.h`, generated from the vendor
  STEP scan, additionally clamps the thumb against a half-curled
  low-rotation pocket the scalar rules above miss. Runs after them, on
  the same targets, converted to the table's own scale at the call site
  (see Geometry collision tables below) — the scalar rules stay the floor.
- **bus lock**: `flock` serializes masters, since two on one NIC make the
  slave refuse OPERATIONAL.
- range clamp 890..1850 (the mechanism's travel — a command below the
  closed end is a stop, not a position), `force<=1000` (default 500),
  `speed 50..1000`.

Guards clamp rather than reject, so a streaming teleop source degrades to
a safe pose instead of failing. `hand_ctl` reports what it changed in
`guarded` / `guard_note`.

**The target scale is defined once**, in `hand_safety.h` — `hs_clamp_target`,
`hs_target_valid`, `hs_ang_to_target`, `hs_target_to_ang`. Nothing else in
the C tree divides by the span or clamps against a literal bound. That
discipline is what made the correction of 2026-08-06 a small change when
the command field turned out to be ANGLEACT counts rather than `0..2000`
(see Axis order above): two `#define`s, two function bodies, and the
constants that were positions on the old scale. Python mirrors it in
`hand_scale.py` and verifies itself against `hand_ctl scale`.

Offline checks, none of which need hardware:

    make test && ./test_safety        # 22 interlock, scale and geometry checks
    python3 test_scale.py             # C and Python agree on the scale
    python3 ../pid/test_pid.py        # the closed-loop trim, against a plant stub
    python3 test_daemon.py            # the daemon, against a simulated slave
    python3 test_teleop_sink.py       # the streaming client path
    python3 test_calibration.py       # profiles cannot be clobbered
    python3 ../camera/test_mapping.py # the mapping is viewpoint-invariant
    python3 test_ui_render.py         # the panel draws, with no display
    python3 test_api_compat.py        # both paths present the same API
    python3 test_release.py           # interrupting anything frees what it held

## Build and setup (from a clean clone)

    ../setup.sh                           # one-shot at repo root: venv, SOEM, cmake, make, cap

⚠️ **Not on the KD240** — `setup.sh` refuses to run on aarch64. The board has
1.9 GB of RAM and no swap, and pip building a vision wheel from source OOMs
it. There, build the C side only:

    export PATH="$HOME/rh56f1_kd240/ethercat/buildenv/bin:$PATH"   # cmake >= 3.28
    make all && make cap

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
spec. Thumb force-sensor calibration awaits vendor F1 documentation.

`fist` did not reach a grip: the four fingers travelled from ANGLEACT
~1790 to only ~1730. That was recorded through `hand_api`, which drives
`hand_ctl`, which cycles at 1 kHz — the rate this hand applies nothing at.
So the observation says little about the mechanism and should be retaken
through `handd`: the same axes covered 1500→1750 in half a second there.
If it still falls short, the next suspect is force and speed, which the
same symptom once responded to at 1000/1000 while `hand_api.pose()`
defaults to 500/800 — but making an unattended hand squeeze harder needs
somebody standing next to it, so it stays untested.

MediaPipe pins XNNPACK to a single thread and will not let you change it,
so the teleop chain runs at **3.9 FPS on the board** when it is
re-detecting (measured 2026-08-06, 320×240, no hand in frame). Tracking is
faster. Getting past that means building the palm→landmark pipeline
directly on `ai-edge-litert`, where the thread count is yours.


## Closed-loop trim (../pid/hand_pid.py)

The firmware executes a pose only after the master disconnects (2-3 s a
shot, no feedback while it runs), so a realtime PID is impossible; what
works is a per-shot integral trim. `HandPID.correct(req, ang_act)`
returns the biased targets for the next shot - held (-1) axes pass
through untouched, the integrator freezes in the deadband / on rails /
on STA 5-6-7, and resets when the target jumps. `req` and `ang_act` are
both ANGLEACT counts (see `hand_scale`, imported across from here), the
same scale `handd`/`hand_ctl`/`hand_set` speak - the module does not
rescale between them. The corrected targets still go through
hand_safety inside hand_ctl/hand_set, so the trim can never bypass the
interlock. Code and offline tests live in `../pid/`: `python3
pid/test_pid.py` (73 checks; convergence needs at most 3 corrected
shots over a +-10% gain and an offset plant).

## Geometry collision tables (hand_collision_table.h)

Generated from the vendor right-hand STEP model (`geometry/`, see
`geometry/README.md`). The tables agree with the empirical interlock
about the f>=600 world (no restriction) and are stricter in the
half-curled low-rotation pocket the scalar rules miss (thumb_bend floors
up to 1250). The scalar rules in `hand_safety.c` REMAIN as the floor: the
model was calibrated on 15 anchor poses with millimetre margins, so only
an on-hand boundary replay (conservative force/speed, poses stepped just
outside the table boundary) may retire them.

The table itself is generated on the mechanism's old, abstract 0..2000
target scale (see its own header comment) - it predates the 2026-08-06
correction that made a target an ANGLEACT count. `hs_interlock()` in
`hand_safety.c` converts at the boundary (`to_hct_scale`/`from_hct_scale`)
rather than regenerate the table, using the same old<->new mapping
documented in `hand_safety.h`. Regenerating after any calibration change
still targets the old scale:

    geometry/venv-geo/bin/python geometry/sample_collisions.py \
        geometry/links.yaml <mesh_dir> <cylinders.json> <grid.npz>
    geometry/venv-geo/bin/python geometry/build_tables.py \
        <grid.npz> geometry/links.yaml hand_collision_table.h

`build_tables` refuses to write the header unless the anchor gates pass.
