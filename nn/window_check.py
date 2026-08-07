#!/usr/bin/env python3
"""Does the window in calibration.json work for the model teleop actually runs?

The window was measured with model_complexity=1. Every app in the repo --
teleop_app.py included -- runs model_complexity=0. thumb_probe.csv holds
both models' answers for the same frames, so the question is answerable
offline: replay the live window against each model's rows and compare.

    ../venv/bin/python3 window_check.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

hm.load_calibration()
HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "thumb_probe.csv"))))


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


def sat(v, lo, hi):
    n = len(v)
    return (100.0 * sum(1 for x in v if x <= lo) / n,
            100.0 * sum(1 for x in v if x >= hi) / n)


print("live window: THUMB_OPEN %.1f  THUMB_CLOSED %.1f  OPP_MIN %.1f  "
      "OPP_MAX %.1f" % (hm.THUMB_OPEN, hm.THUMB_CLOSED, hm.OPP_MIN, hm.OPP_MAX))
print("output range: bend %d..%d   rot %d..%d\n"
      % (hm.T_MIN, hm.T_MAX, hm.ROT_MIN, hm.T_MAX))

per = {}
for model in ("lite", "full"):
    R = [r for r in rows if r["model"] == model and r["trust"] == "1"]
    if not R:
        continue
    per[model] = {int(r["frame"]): r for r in R}
    flex = [float(r["flexion"]) for r in R]
    opp = [float(r["opposition"]) for r in R]
    bend = [hm._scale(x, hm.THUMB_CLOSED, hm.THUMB_OPEN, hm.T_MIN, hm.T_MAX)
            for x in flex]
    rot = [hm._scale(x, hm.OPP_MAX, hm.OPP_MIN, hm.ROT_MIN, hm.T_MAX)
           for x in opp]
    bl, bh = sat(bend, hm.T_MIN, hm.T_MAX)
    rl, rh = sat(rot, hm.ROT_MIN, hm.T_MAX)
    print("=== %s (complexity %d) === %d trusted of %d frames (%.1f%%)"
          % (model, 0 if model == "lite" else 1, len(R),
             sum(1 for r in rows if r["model"] == model),
             100.0 * len(R) / sum(1 for r in rows if r["model"] == model)))
    print("  flexion    p2 %6.1f  p50 %6.1f  p98 %6.1f   raw %.1f..%.1f"
          % (pct(flex, 2), pct(flex, 50), pct(flex, 98), min(flex), max(flex)))
    print("  tgt_bend   %d..%d   travel used %.1f%%   floor %.1f%%  ceil %.1f%%"
          % (min(bend), max(bend),
             100.0 * (max(bend) - min(bend)) / (hm.T_MAX - hm.T_MIN), bl, bh))
    print("  opposition p2 %6.1f  p50 %6.1f  p98 %6.1f   raw %.1f..%.1f"
          % (pct(opp, 2), pct(opp, 50), pct(opp, 98), min(opp), max(opp)))
    print("  tgt_rot    %d..%d   travel used %.1f%%   floor %.1f%%  ceil %.1f%%"
          % (min(rot), max(rot),
             100.0 * (max(rot) - min(rot)) / (hm.T_MAX - hm.ROT_MIN), rl, rh))
    print()

if "lite" in per and "full" in per:
    shared = sorted(set(per["lite"]) & set(per["full"]))
    print("=== same-frame disagreement === %d frames both models trusted"
          % len(shared))
    for k in ("flexion", "abduction", "opposition"):
        d = [float(per["full"][f][k]) - float(per["lite"][f][k]) for f in shared]
        a = sorted(abs(x) for x in d)
        print("  %-11s full-minus-lite  median %+6.1f   |diff| p50 %5.1f  "
              "p90 %5.1f  max %6.1f" % (k, pct(d, 50), a[len(a) // 2],
                                        a[int(len(a) * 0.9)], a[-1]))
    print("\n  a positive median means full reads the angle larger than lite,")
    print("  so a window measured on full sits high for lite's numbers.")
    print("\n=== what the window would be if measured on lite ===")
    lf = [float(per["lite"][f]["flexion"]) for f in shared]
    lo = [float(per["lite"][f]["opposition"]) for f in shared]
    print("  THUMB_OPEN  p2  %6.1f   (live %.1f)" % (pct(lf, 2), hm.THUMB_OPEN))
    print("  THUMB_CLOSED p98 %5.1f   (live %.1f)"
          % (pct(lf, 98), hm.THUMB_CLOSED))
    print("  OPP_MIN     p2  %6.1f   (live %.1f)" % (pct(lo, 2), hm.OPP_MIN))
    print("  OPP_MAX     p98 %5.1f   (live %.1f)" % (pct(lo, 98), hm.OPP_MAX))
