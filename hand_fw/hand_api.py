"""hand_fw.hand_api - Python API for the Inspire RH56F1 dexterous hand.

Wraps the SOEM-based `hand_ctl` binary (EtherCAT, .28 built-in RJ45).
Axis order everywhere: [pinky, ring, middle, index, thumb_bend, thumb_rot]
Targets: 0 = fully closed, 2000 = fully open, -1 = leave unchanged.

F1 quirks handled here:
  - pose executes when the EtherCAT master disconnects -> each call is one
    connect/wake/write/disconnect cycle (takes ~5-15 s incl. wake)
  - index+thumb deep-close collision -> guarded in hand_ctl; fist() staggers
    fingers and thumb into two phases automatically
"""
import json
import subprocess
import time
from pathlib import Path

HAND_CTL = str(Path(__file__).resolve().parent / "hand_ctl")
SETTLE_S = 6  # wait after disconnect for the pose to physically execute

OPEN = 2000
CLOSED = 0
KEEP = -1


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
    """One method call = one executed pose (or a telemetry read)."""

    def state(self):
        """Read telemetry without moving anything."""
        return _run(["state"])

    def pose(self, targets, force=500, speed=800, settle=True):
        """Execute a 6-axis pose. targets: list of 6 ints (0..2000 or -1)."""
        if len(targets) != 6:
            raise HandError("need exactly 6 targets")
        out = _run(["pose"] + [str(int(t)) for t in targets] +
                   [str(int(force)), str(int(speed))])
        if settle:
            time.sleep(SETTLE_S)
        return out

    # ---- gesture library ----------------------------------------------
    def open_hand(self, **kw):
        return self.pose([OPEN] * 5 + [KEEP], **kw)

    def fist(self, grip=300, **kw):
        """Safe two-phase fist: fingers first, thumb afterwards (collision
        guard forbids closing index and thumb together)."""
        first = self.pose([grip, grip, grip, grip, KEEP, KEEP], **kw)
        second = self.pose([KEEP, KEEP, KEEP, KEEP, 700, KEEP], **kw)
        return {"phase1": first, "phase2": second}

    def middle_finger(self, **kw):
        """Fingers+thumb down, middle up. Thumb staggered for safety."""
        first = self.pose([CLOSED, CLOSED, OPEN, 700, KEEP, KEEP], **kw)
        second = self.pose([KEEP, KEEP, KEEP, KEEP, 800, KEEP], **kw)
        return {"phase1": first, "phase2": second}

    def point(self, **kw):
        """Index extended, others folded (index open so no thumb conflict)."""
        return self.pose([CLOSED, CLOSED, CLOSED, OPEN, 700, KEEP], **kw)

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
