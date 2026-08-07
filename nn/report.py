"""Re-print the thumb_probe summary from an existing CSV."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

CH = ("flexion", "abduction", "opposition")
NAME = {0: "lite", 1: "full"}
OPP_LO = getattr(hm, "OPP_MIN", getattr(hm, "ABD_MIN", 10.0))
OPP_HI = getattr(hm, "OPP_MAX", getattr(hm, "ABD_MAX", 90.0))

rows = list(csv.DictReader(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "thumb_probe.csv"))))
for r in rows:
    for k in CH + ("t",):
        r[k] = float(r[k])
    for k in ("frame", "complexity", "trust", "trust_majority",
              "tgt_bend", "tgt_rot"):
        r[k] = int(r[k])
per = {0: {}, 1: {}}
for r in rows:
    per[r["complexity"]][r["frame"]] = r
read = max(r["frame"] for r in rows)
span = max(r["t"] for r in rows)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


print("=== A. coverage ===  %d rows, frames up to %d over %.1fs"
      % (len(rows), read, span))
for c in (0, 1):
    tr = [r for r in per[c].values() if r["trust"]]
    maj = [r for r in per[c].values() if r["trust_majority"]]
    why = {}
    for r in per[c].values():
        if not r["trust"]:
            why[r["why"]] = why.get(r["why"], 0) + 1
    print("  %-4s hand on %3d frames (%4.1f%%), trusted %3d (%4.1f%%)"
          % (NAME[c], len(per[c]), 100.0 * len(per[c]) / read,
             len(tr), 100.0 * len(tr) / read))
    print("       majority-lock would trust %d (%+d)" % (len(maj), len(tr) - len(maj)))
    if why:
        print("       rejected: " + ", ".join("%s x%d" % kv for kv in sorted(why.items())))
both = sorted(f for f in set(per[0]) & set(per[1])
              if per[0][f]["trust"] and per[1][f]["trust"])
print("  both saw %d, both trusted %d"
      % (len(set(per[0]) & set(per[1])), len(both)))
if not both:
    sys.exit("nothing comparable")

print("\n=== B. paired, same %d frames ===" % len(both))
for k in CH:
    d = sorted(abs(per[1][f][k] - per[0][f][k]) for f in both)
    print("    %-11s |full-lite|  p50 %6.1f  p90 %6.1f  max %6.1f"
          % (k, d[len(d) // 2], d[int(len(d) * 0.9)], d[-1]))
g = [per[1][f]["opposition"] - per[0][f]["opposition"] for f in both]
print("    opposition gaps within 20 of 0 or +-360: %d/%d"
      % (sum(1 for x in g if min(abs(abs(x) - 360), abs(x)) < 20), len(g)))

print("\n=== C. per model on those %d frames ===" % len(both))
for c in (0, 1):
    print("  --- %s ---" % NAME[c])
    for k in CH:
        v = [per[c][f][k] for f in both]
        print("    %-11s min %7.1f  p2 %7.1f  p50 %7.1f  p98 %7.1f  max %7.1f"
              % (k, min(v), pct(v, 2), pct(v, 50), pct(v, 98), max(v)))

print("\n=== D. targets under the current window ===")
for c in (0, 1):
    b = [per[c][f]["tgt_bend"] for f in both]
    o = [per[c][f]["tgt_rot"] for f in both]
    print("  %-4s tgt_bend %4d..%4d sat %5.1f%%   tgt_rot %4d..%4d sat %5.1f%%"
          % (NAME[c], min(b), max(b),
             100.0 * sum(1 for x in b if x in (hm.T_MIN, hm.T_MAX)) / len(b),
             min(o), max(o),
             100.0 * sum(1 for x in o if x in (hm.ROT_MIN, hm.T_MAX)) / len(o)))

print("\n=== E. window a calibration run would write ===")
for c in (0, 1):
    tr = [f for f in per[c] if per[c][f]["trust"]]
    if not tr:
        continue
    t = [per[c][f]["flexion"] for f in tr]
    o = [per[c][f]["opposition"] for f in tr]
    print("  %-4s raw  OPEN %6.1f  CLOSED %6.1f  OPP %6.1f..%6.1f"
          % (NAME[c], min(t), max(t), min(o), max(o)))
    print("  %-4s p2/98 OPEN %6.1f  CLOSED %6.1f  OPP %6.1f..%6.1f"
          % (NAME[c], pct(t, 2), pct(t, 98), pct(o, 2), pct(o, 98)))
print("  in use: OPEN %s  CLOSED %s  OPP %s..%s"
      % (hm.THUMB_OPEN, hm.THUMB_CLOSED, OPP_LO, OPP_HI))

print("\n=== F. did the recording actually sweep each channel? ===")
for c in (0, 1):
    tr = [per[c][f] for f in sorted(per[c]) if per[c][f]["trust"]]
    if not tr:
        continue
    print("  --- %s ---" % NAME[c])
    for b in range(0, int(span) + 1, 4):
        sel = [r for r in tr if b <= r["t"] < b + 4]
        if not sel:
            print("    %2d-%2ds  (nothing trusted)" % (b, b + 4))
            continue
        print("    %2d-%2ds  n=%3d  flex %5.1f..%5.1f  abd %5.1f..%5.1f  "
              "opp %6.1f..%6.1f"
              % (b, b + 4, len(sel),
                 min(r["flexion"] for r in sel), max(r["flexion"] for r in sel),
                 min(r["abduction"] for r in sel), max(r["abduction"] for r in sel),
                 min(r["opposition"] for r in sel), max(r["opposition"] for r in sel)))
