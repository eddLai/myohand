"""Turn the sweep into a correction table, without guessing its shape.

Every functional form tried so far has been wrong, and each was wrong in the
same way: it assumed the invention grows smoothly out of nothing. It does not.
Flexion is flat across 80 degrees of sweep, then gains 30 in the next four.
A line cannot do that, and a hinge cannot either; whatever fits the cliff
overfits the flat part, and whatever fits the flat part misses the cliff.

Eight measured points do not need a shape. The correction at each is the
distance from the flat baseline, and between them it interpolates. That is
honest about what was measured and silent about what was not.

Below the cliff the measured distances are small and unsigned noise - the flat
region scatters six degrees around its own median - so they are pinned to
zero. Correcting noise into the signal is how earlier versions turned an
honest 31 degree reading into 7.
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "sweep_probe.csv"))))
for r in rows:
    r["flexion"] = float(r["flexion"])
    r["opposition"] = float(r["opposition"])
    r["target"] = int(r["target"])


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def points(model):
    out = []
    for t in sorted({r["target"] for r in rows}):
        sel = [r for r in rows if r["model"] == model and r["target"] == t
               and r["trust"] == "1"]
        if len(sel) >= 5:
            out.append((med([r["opposition"] for r in sel]),
                        med([r["flexion"] for r in sel])))
    return sorted(out)


for model in ("lite", "full"):
    pts = points(model)
    # the cliff is the largest step between neighbours; everything before it
    # is the flat region the baseline comes from
    jumps = [(pts[i + 1][1] - pts[i][1], i) for i in range(len(pts) - 1)]
    big, i = max(jumps)
    flat = pts[:i + 1]
    base = med([f for _, f in flat])
    scatter = max(f for _, f in flat) - min(f for _, f in flat)

    print("=== %s ===" % model)
    print("  平的那段 %d 個點，基準 %.1f，本身散佈 %.1f" % (len(flat), base, scatter))
    print("  斷崖落在 %.1f 到 %.1f 之間，跳 %.1f 度"
          % (pts[i][0], pts[i + 1][0], big))
    print("  %10s %10s %12s" % ("對掌", "彎曲", "要減掉"))
    table = []
    for o, f in pts:
        corr = 0.0 if o <= pts[i][0] else round(f - base, 1)
        corr = max(0.0, corr)
        table.append([round(o, 1), corr])
        print("  %10.1f %10.1f %12.1f" % (o, f, corr))
    print("  OPP_LEAK_TABLE = %s" % table)

    # what it leaves behind, against the scatter of a single held angle
    def interp(o):
        if o <= table[0][0]:
            return table[0][1]
        if o >= table[-1][0]:
            return table[-1][1]
        for (a, ca), (b, cb) in zip(table, table[1:]):
            if a <= o <= b:
                return ca + (cb - ca) * (o - a) / (b - a) if b > a else ca
        return 0.0

    resid = [abs(f - interp(o) - base) for o, f in pts]
    print("  修正後離基準最遠 %.1f 度（單一角度自己就抖 %.1f）\n"
          % (max(resid), scatter))
