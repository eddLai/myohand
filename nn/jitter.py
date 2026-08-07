"""How much do the raw thumb channels move between consecutive frames?

The calibration window fixes the scale. It cannot fix a channel that
changes faster than the hand does. A thumb moving at a human pace should
change a few degrees per frame at 30 fps; anything much larger is the
landmark estimate moving, not the thumb.
"""
import csv
import os

rows = [r for r in csv.DictReader(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "thumb_probe.csv")))]
CH = ("flexion", "abduction", "opposition")

for model in ("lite", "full"):
    R = [r for r in rows if r["model"] == model and r["trust"] == "1"]
    R.sort(key=lambda r: int(r["frame"]))
    if len(R) < 2:
        continue
    print("=== %s === %d trusted frames" % (model, len(R)))
    for k in CH:
        d = []
        for a, b in zip(R, R[1:]):
            if int(b["frame"]) - int(a["frame"]) != 1:
                continue                      # only truly consecutive frames
            d.append(abs(float(b[k]) - float(a[k])))
        if not d:
            continue
        d.sort()
        big = sum(1 for x in d if x > 20)
        print("  %-11s consecutive-frame change: p50 %5.1f  p90 %5.1f  "
              "max %6.1f   >20 deg in one frame: %d/%d (%.1f%%)"
              % (k, d[len(d) // 2], d[int(len(d) * 0.9)], d[-1],
                 big, len(d), 100.0 * big / len(d)))
    print()

print("at 30 fps a thumb sweeping its full ~100 deg range in half a second")
print("moves about 7 deg per frame. jumps well past that are the estimate.")
