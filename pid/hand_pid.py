"""hand_pid - slow outer-loop trim for RH56F1 targets.

The firmware executes a pose only after the master disconnects, so one
shot costs 2-3 s and gives no feedback while it runs. A real-time inner
PID is impossible on this protocol; what works is a per-shot integral
trim: after each shot, compare the requested target with the ANGLEACT
readback and bias the next shot to cancel the steady-state offset.

This module is a pure corrector - it never talks to the hand. Wire it
into your own EtherCAT call path:

    from hand_pid import HandPID
    pid = HandPID()
    tgt = pid.correct(req, ang)          # ang = ANGLEACT from the last shot
    # send tgt through hand_set / hand_api as usual
    tgt = pid.correct(req, ang, sta=sta) # pass STA to freeze on 5/6/7

Corrected targets still pass through hand_safety's interlock inside
hand_ctl/hand_set before they reach the PDO, so this module cannot
command a clash. Held axes (-1) are never corrected.
"""

import json
import os
import time

import hand_scale

AXES = 6


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# req and ang are both ANGLEACT counts (see hand_scale) - the same scale,
# so nothing here recomputes it. Kept as a name so a caller reads intent.
ang_to_target = hand_scale.ang_to_target


class HandPID:
    """Per-axis integral trim, one step per shot.

    e = req - ang_to_target(ang);  I += e;  out = req + Ki * I

    The integrator freezes inside the deadband and while the output sits
    on a rail with the error still pushing outward, and resets when the
    target jumps (new gesture), when the axis reports STA 5/6/7, when
    the axis is held (-1), and on reset().
    """

    BAD_STA = (5, 6, 7)  # current-protect / stall / standby: readback lies

    def __init__(self, ki=(0.8, 0.8, 0.8, 0.8, 0.6, 0.6), tol=25,
                 gesture_jump=120, i_limit=300, log_path=None):
        self.ki = list(ki)
        self.tol = tol
        self.gesture_jump = gesture_jump
        # cap the correction itself at i_limit units, whatever Ki is
        self.i_max = [i_limit / k if k > 0 else 0.0 for k in self.ki]
        self.log_path = log_path
        self.reset()

    def reset(self, axes=None):
        if axes is None:
            axes = range(AXES)
        if not hasattr(self, "integ"):
            self.integ = [0.0] * AXES
            self.prev_req = [None] * AXES
            self.prev_out = [None] * AXES
            self.last_err = [0] * AXES
        for i in axes:
            self.integ[i] = 0.0
            self.prev_req[i] = None
            self.prev_out[i] = None
            self.last_err[i] = 0

    def correct(self, req, ang, sta=None):
        """Return the trimmed 6-axis target list for the next shot."""
        if len(req) != AXES or len(ang) != AXES:
            raise ValueError("need %d-axis req and ang" % AXES)
        out = [0] * AXES
        for i in range(AXES):
            r = req[i]
            if r == -1:                      # held axis: pure pass-through
                self.reset([i])
                out[i] = -1
                continue
            r = clamp(int(r), hand_scale.TARGET_MIN, hand_scale.TARGET_MAX)
            if self.prev_req[i] is not None and \
                    abs(r - self.prev_req[i]) > self.gesture_jump:
                self.reset([i])              # new gesture, old bias is stale
            if sta is not None and sta[i] in self.BAD_STA:
                self.reset([i])              # mechanically blocked: don't push
                self.prev_req[i] = r
                self.last_err[i] = 0
                out[i] = r
                continue
            e = r - ang_to_target(ang[i])
            self.last_err[i] = e
            # first shot at this target: the readback is still the old
            # pose, so the error says nothing about this target yet
            fresh = self.prev_req[i] is None
            po = self.prev_out[i]
            railed = po is not None and \
                ((po >= hand_scale.TARGET_MAX and e > 0) or
                 (po <= hand_scale.TARGET_MIN and e < 0))
            if not fresh and abs(e) > self.tol and not railed:
                self.integ[i] = clamp(self.integ[i] + e,
                                      -self.i_max[i], self.i_max[i])
            u = clamp(int(round(r + self.ki[i] * self.integ[i])),
                      hand_scale.TARGET_MIN, hand_scale.TARGET_MAX)
            self.prev_req[i] = r
            self.prev_out[i] = u
            out[i] = u
        return out

    def settled(self):
        """True when every non-held axis ended the last shot inside tol."""
        return all(abs(e) <= self.tol for e in self.last_err)

    def log(self, shot):
        """Append one shot dict as a JSONL line (adds ts/integ/ki)."""
        if not self.log_path:
            return
        rec = dict(shot)
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        rec["integ"] = [round(v, 1) for v in self.integ]
        rec["ki"] = self.ki
        d = os.path.dirname(self.log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
