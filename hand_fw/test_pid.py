"""Offline tests for hand_pid - no hardware, plain `python3 test_pid.py`.

The plant stub models what the outer loop actually fights: a per-axis
gain/offset error on the reached pose, ang = target_scale(g*u + b),
with the readback expressed on the ANGLEACT scale like the real hand.
"""

from hand_pid import HandPID, ANG_CLOSED, ANG_OPEN, ang_to_target, clamp

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL %s %s" % (name, detail))


def plant(u, g, b):
    """Reached targets -> ANGLEACT readback, with gain/offset error."""
    ang = []
    for v in u:
        reached = clamp(g * v + b, 0, 2000)
        ang.append(ANG_CLOSED + reached * (ANG_OPEN - ANG_CLOSED) / 2000.0)
    return ang


def run_shots(pid, req, g, b, shots):
    """Send req repeatedly; return per-shot |e| history of axis 0."""
    errs, outs = [], []
    ang = plant(req, g, b)                  # first shot: uncorrected
    for _ in range(shots):
        out = pid.correct(req, ang)
        outs.append(out[0])
        ang = plant(out, g, b)
        errs.append(abs(req[0] - ang_to_target(ang[0])))
    return errs, outs


def test_convergence():
    for g in (0.9, 1.0, 1.1):
        for b in (-80, 0, 80):
            for target in (400, 1000, 1600):
                pid = HandPID()
                req = [target] * 6
                errs, outs = run_shots(pid, req, g, b, 6)
                within = next((k for k, e in enumerate(errs) if e <= pid.tol),
                              None)
                check("converge g=%s b=%s t=%s" % (g, b, target),
                      within is not None and within < 3,
                      "errs=%s" % errs)
                # no oscillation: correction steps never flip sign and grow
                steps = [outs[k + 1] - outs[k] for k in range(len(outs) - 1)]
                grew = any(steps[k] * steps[k + 1] < 0 and
                           abs(steps[k + 1]) > abs(steps[k])
                           for k in range(len(steps) - 1))
                check("no-osc g=%s b=%s t=%s" % (g, b, target), not grew,
                      "outs=%s" % outs)


def test_hold_passthrough():
    pid = HandPID()
    req = [1000, 1000, 1000, -1, 1000, -1]
    ang = plant([1000] * 6, 0.9, -80)
    for _ in range(4):
        out = pid.correct(req, ang)
        check("hold stays -1", out[3] == -1 and out[5] == -1, str(out))
        ang = plant([v if v != -1 else 1000 for v in out], 0.9, -80)
    check("hold integ zero", pid.integ[3] == 0 and pid.integ[5] == 0,
          str(pid.integ))


def test_deadband():
    pid = HandPID()
    req = [1000] * 6
    ang = plant(req, 1.0, 10)               # |e| ~ 10 < tol
    out = pid.correct(req, ang)
    check("deadband no trim", out == req, str(out))
    check("deadband no integ", all(v == 0 for v in pid.integ),
          str(pid.integ))
    check("settled", pid.settled())


def test_gesture_reset():
    pid = HandPID()
    req = [1000] * 6
    ang = plant(req, 0.9, -80)
    for _ in range(3):
        out = pid.correct(req, ang)
        ang = plant(out, 0.9, -80)
    check("bias built up", pid.integ[0] != 0, str(pid.integ))
    req2 = [300] * 6                        # jump > gesture_jump
    out = pid.correct(req2, ang)
    check("gesture reset -> raw req", out == req2, str(out))


def test_sta_freeze():
    pid = HandPID()
    req = [500] * 6
    ang = plant(req, 0.9, -80)
    pid.correct(req, ang)
    sta = [2, 2, 5, 2, 6, 2]
    out = pid.correct(req, ang, sta=sta)
    check("sta axes uncorrected", out[2] == 500 and out[4] == 500, str(out))
    check("sta integ reset", pid.integ[2] == 0 and pid.integ[4] == 0,
          str(pid.integ))


def test_antiwindup():
    pid = HandPID()
    req = [1800] * 6
    g, b = 0.85, -80                        # plant tops out at 1620: stuck
    ang = plant(req, g, b)
    for _ in range(10):
        out = pid.correct(req, ang)
        ang = plant(out, g, b)
    check("integ bounded", all(abs(v) <= m + 1e-9
                               for v, m in zip(pid.integ, pid.i_max)),
          str(pid.integ))
    check("output railed not beyond", all(0 <= v <= 2000 for v in out))
    # once the obstruction clears, the loop must bleed off the rail bias
    # within a few shots and never push past the rail
    ang = plant(out, 1.0, 0)
    for _ in range(4):
        out = pid.correct(req, ang)
        check("no push past rail", all(v <= 2000 for v in out), str(out))
        ang = plant(out, 1.0, 0)
    e = abs(req[0] - ang_to_target(ang[0]))
    check("recovers after clear", e <= 35, "e=%s out=%s" % (e, out))


def main():
    test_convergence()
    test_hold_passthrough()
    test_deadband()
    test_gesture_reset()
    test_sta_freeze()
    test_antiwindup()
    print("%d passed, %d failed" % (PASS, FAIL))
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
