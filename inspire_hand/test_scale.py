#!/usr/bin/env python3
"""Is there still exactly one definition of the target scale?

The scale is the single most likely thing in this tree to be edited in
one place and forgotten in another - the command field may not be 0..2000
at all (see hand_safety.h), and the day that is settled, three files have
to agree. So this checks the properties rather than the numbers, and then
asks the C binary whether Python still matches it.

    python3 test_scale.py        (no hardware; `hand_ctl scale` moves nothing)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hand_mapping as hm
import hand_scale as sc

fails = 0


def check(name, cond):
    global fails
    print(f"{name:<46s} {'ok' if cond else 'FAIL'}")
    if not cond:
        fails += 1


check("closed ANGLEACT maps to the bottom target",
      sc.ang_to_target(sc.ANG_CLOSED) == sc.TARGET_MIN)
check("open ANGLEACT maps to the top target",
      sc.ang_to_target(sc.ANG_OPEN) == sc.TARGET_MAX)
check("out-of-span ANGLEACT clamps both ways",
      sc.ang_to_target(sc.ANG_CLOSED - 500) == sc.TARGET_MIN
      and sc.ang_to_target(sc.ANG_OPEN + 500) == sc.TARGET_MAX)
check("ang -> target is monotonic",
      all(sc.ang_to_target(a) <= sc.ang_to_target(a + 10)
          for a in range(sc.ANG_CLOSED, sc.ANG_OPEN, 10)))
check("target -> ang -> target round-trips within a step",
      all(abs(sc.ang_to_target(sc.target_to_ang(t)) - t) <= 3
          for t in range(sc.TARGET_MIN, sc.TARGET_MAX + 1, 50)))
check("a hold stays a hold through both conversions",
      sc.clamp_target(sc.TARGET_HOLD) == sc.TARGET_HOLD
      and sc.target_to_ang(sc.TARGET_HOLD) == sc.TARGET_HOLD)
check("clamp pulls overshoot to the ends",
      sc.clamp_target(sc.TARGET_MAX + 3000) == sc.TARGET_MAX
      and sc.clamp_target(sc.TARGET_MIN - 3000) == sc.TARGET_MIN)
check("a hold is valid, other out-of-range values are not",
      sc.target_valid(sc.TARGET_HOLD) and not sc.target_valid(sc.TARGET_MAX + 1)
      and not sc.target_valid(sc.TARGET_MIN - 2))

# the mapping must read the scale rather than carry its own copy
check("hand_mapping reads the scale instead of restating it",
      hm.T_MAX == sc.TARGET_MAX
      and hm.target_from_angle is sc.ang_to_target)
check("mapped targets never leave the scale",
      sc.TARGET_MIN <= hm.T_MIN <= hm.T_MAX <= sc.TARGET_MAX)

# and the C definition has to agree with this one
verdict = sc.verify()
if verdict is None:
    check("C and Python agree on the scale", True)
elif verdict.startswith("skipped"):
    print(f"{'C and Python agree on the scale':<46s} skipped ({verdict[9:]})")
else:
    check(f"C and Python agree on the scale ({verdict})", False)

print("\n" + ("FAILURES PRESENT" if fails else "all checks passed"))
sys.exit(1 if fails else 0)
