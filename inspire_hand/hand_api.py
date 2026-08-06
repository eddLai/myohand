"""inspire_hand.hand_api - Python API for the Inspire RH56F1 dexterous hand.

Axis order everywhere: [pinky, ring, middle, index, thumb_bend, thumb_rot]
Targets are ANGLEACT counts: ~890 = fully closed, ~1850 = fully open,
-1 = leave unchanged. The scale lives in hand_scale.py / hand_safety.h.

Two paths, one surface. If `handd` is running this talks to it over its
unix socket and a pose costs a couple of milliseconds; if it is not, this
spawns `hand_ctl` exactly as it always did and a pose costs seconds. The
method names, their arguments and the dict they return are identical
either way, so nothing that imports this module has to know or care -
that is the whole point, and it is why `pose()` keeps taking `force` and
`speed` per call even though the daemon used to have them only as
start-up flags (a `profile` command was added to handd so the argument
still means something rather than being quietly dropped).

Read `.via` if you do want to know: "daemon" or "hand_ctl".

F1 quirks handled here:
  - the hand applies a pose continuously while process data arrives, but
    only if it arrives no faster than about 625 Hz - above that its
    control loop never finishes and nothing moves at all. handd defaults
    to 500 Hz. The old belief that a pose executed only on disconnect was
    this same effect seen from the wrong side; see the vault's
    Execution_Trigger_Settled
  - index+thumb deep-close collision -> guarded below the API in either
    path; fist() staggers fingers and thumb into two phases automatically
"""
import json
import subprocess
import time
from pathlib import Path

import hand_scale

HAND_CTL = str(Path(__file__).resolve().parent / "hand_ctl")
# hand_ctl now cycles at 500 Hz, so the pose executes during its own hold
# and the telemetry it returns is the pose that happened. What is left to
# wait for is the tail of the axis's travel, not a watchdog and not a
# disconnect. Measured 2026-08-06: a pose call returns in about 1.9 s all
# in, against 10-20 s before, and full travel is 800 ms of that.
SETTLE_S = 1
# On the daemon path there is no disconnect to wait for, so instead of
# sleeping a fixed time we watch the hand until it stops moving. The cap
# is a little over the 800 ms a finger needs for full travel.
SETTLE_POLL_S = 0.05
SETTLE_STILL_READS = 3
SETTLE_MAX_S = 1.5
SETTLE_EPS = 8          # ANGLEACT counts; below this the axis has arrived

OPEN = hand_scale.TARGET_MAX
CLOSED = hand_scale.TARGET_MIN
KEEP = hand_scale.TARGET_HOLD


class HandError(RuntimeError):
    pass


def _run(args, timeout=40):
    try:
        r = subprocess.run([HAND_CTL] + args, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HandError("hand_ctl timeout")
    line = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        raise HandError(f"bad hand_ctl output: {r.stdout!r} {r.stderr!r}")
    if not data.get("ok"):
        raise HandError(data.get("error", "unknown hand_ctl error"))
    return data


class InspireHand:
    """One method call = one executed pose (or a telemetry read).

    Uses `handd` when it is up and `hand_ctl` when it is not. Pass
    use_daemon=False to force the subprocess path (useful when something
    else owns the daemon, or to reproduce an old measurement)."""

    def __init__(self, use_daemon=True, socket_path=None):
        self._client = None
        self._profile = None
        if use_daemon:
            self._client = self._try_daemon(socket_path)

    @staticmethod
    def _try_daemon(socket_path):
        """Connect if handd is there. Any failure means it is not, and the
        subprocess path is a complete fallback, so this never raises."""
        try:
            import hand_client
        except ImportError:
            return None
        try:
            kw = {"path": socket_path} if socket_path else {}
            return hand_client.HandClient(**kw).connect()
        except Exception:
            return None

    @property
    def via(self):
        """Which path this instance is using: "daemon" or "hand_ctl"."""
        return "daemon" if self._client else "hand_ctl"

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    # `with InspireHand() as hand:` so a caller can guarantee the daemon
    # connection is dropped on every path, including an exception. The
    # kernel would close the socket at exit anyway; what this buys is a
    # long-lived program - hand_server, a notebook - not accumulating one
    # connection per handler while the daemon counts them against
    # MAX_CLIENTS.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def state(self):
        """Read telemetry without moving anything."""
        if not self._client:
            return _run(["state"])
        return self._normalise(self._client.state(), mode="state")

    def pose(self, targets, force=500, speed=800, settle=True):
        """Execute a 6-axis pose. targets: 6 ints (890..1850, or -1)."""
        if len(targets) != 6:
            raise HandError("need exactly 6 targets")
        if not self._client:
            out = _run(["pose"] + [str(int(t)) for t in targets] +
                       [str(int(force)), str(int(speed))])
            if settle:
                time.sleep(SETTLE_S)
            return out

        try:
            if self._profile != (force, speed):
                self._client.profile(force, speed)
                self._profile = (force, speed)
            ack = self._client.target([int(t) for t in targets])
        except Exception as e:
            # A daemon that died mid-session must not take the caller with
            # it: fall back for this call and for the rest of the session.
            self._client = None
            self._profile = None
            return self.pose(targets, force, speed, settle)
        if settle:
            self._wait_still()
        # state() has already normalised, and _normalise uses setdefault so
        # it will not overwrite the "state" it put there - say what this
        # call actually was, explicitly.
        out = self.state()
        out["mode"] = "pose"
        out["guarded"] = ack.get("guarded", 0)
        out["guard_note"] = ack.get("guard_note", "")
        return out

    def _wait_still(self):
        """Poll until the axes stop moving, instead of sleeping a constant.

        The hand_ctl path sleeps SETTLE_S because it has nothing to watch -
        the link is down. Here telemetry is live, so "settled" can mean
        what it says."""
        deadline = time.time() + SETTLE_MAX_S
        still = 0
        prev = None
        while time.time() < deadline and still < SETTLE_STILL_READS:
            ang = self.state().get("ang")
            if prev is not None and ang is not None and \
                    all(abs(a - b) <= SETTLE_EPS for a, b in zip(ang, prev)):
                still += 1
            else:
                still = 0
            prev = ang
            time.sleep(SETTLE_POLL_S)

    @staticmethod
    def _normalise(reply, mode):
        """Give the daemon's reply the shape hand_ctl's callers expect.

        The two speak the same telemetry keys already; what the daemon
        does not send is the mode/guarded/guard_note trio, and what it
        does send that hand_ctl never did (bus, simulate) is additive and
        harmless. Filling the gap here means a caller written against
        hand_ctl keeps working unchanged."""
        if reply.get("bus") == "down":
            raise HandError(reply.get("note", "daemon reports the bus down"))
        out = dict(reply)
        out.setdefault("mode", mode)
        out.setdefault("guarded", 0)
        out.setdefault("guard_note", "")
        return out

    # ---- gesture library ----------------------------------------------
    def open_hand(self, **kw):
        return self.pose([OPEN] * 5 + [KEEP], **kw)

    # The gesture constants below moved with the 2026-08-06 scale
    # correction: each names the position its old 0..2000 value named
    # (grip 300 -> 1034, 700 -> 1226, 800 -> 1274).
    def fist(self, grip=1034, **kw):
        """Safe two-phase fist: fingers first, thumb afterwards (collision
        guard forbids closing index and thumb together)."""
        first = self.pose([grip, grip, grip, grip, KEEP, KEEP], **kw)
        second = self.pose([KEEP, KEEP, KEEP, KEEP, 1226, KEEP], **kw)
        return {"phase1": first, "phase2": second}

    def middle_finger(self, **kw):
        """Fingers+thumb down, middle up. Thumb staggered for safety."""
        first = self.pose([CLOSED, CLOSED, OPEN, 1226, KEEP, KEEP], **kw)
        second = self.pose([KEEP, KEEP, KEEP, KEEP, 1274, KEEP], **kw)
        return {"phase1": first, "phase2": second}

    def point(self, **kw):
        """Index extended, others folded (index open so no thumb conflict)."""
        return self.pose([CLOSED, CLOSED, CLOSED, OPEN, 1226, KEEP], **kw)

    def release(self, **kw):
        """Alias of open_hand - park pose for leaving the hand unattended."""
        return self.open_hand(**kw)


if __name__ == "__main__":
    import sys
    hand = InspireHand()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    if cmd == "state":
        print(json.dumps(hand.state(), indent=1))
    elif cmd == "open":
        print(json.dumps(hand.open_hand(), indent=1))
    elif cmd == "fist":
        print(json.dumps(hand.fist(), indent=1))
    elif cmd == "middle":
        print(json.dumps(hand.middle_finger(), indent=1))
    elif cmd == "point":
        print(json.dumps(hand.point(), indent=1))
    elif cmd == "pose":
        print(json.dumps(hand.pose([int(x) for x in sys.argv[2:8]]), indent=1))
    else:
        print("usage: hand_api.py [state|open|fist|middle|point|pose P R M I TB TR]")
