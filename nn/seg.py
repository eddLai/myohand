import csv
import os

rows = [r for r in csv.DictReader(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "thumb_calib.csv")))
    if r["trust"] == "1"]
for r in rows:
    r["t"] = float(r["t"])
    for k in ("flexion", "opposition", "abduction"):
        r[k] = float(r[k])

LABEL = {0: "fist/open   ", 5: "splay       ", 10: "ROTATE      ",
         15: "curl        "}
print("segment        n   flexion            opposition          abduction")
for b in (0, 5, 10, 15):
    s = [r for r in rows if b <= r["t"] < b + 5]
    if not s:
        print("%s %4d  (nothing trusted)" % (LABEL[b], 0))
        continue
    def rng(k):
        v = sorted(r[k] for r in s)
        return "%6.1f..%6.1f" % (v[0], v[-1])
    print("%s %4d  %s   %s   %s"
          % (LABEL[b], len(s), rng("flexion"), rng("opposition"),
             rng("abduction")))

print("\nopposition over time, 1s buckets (trusted only):")
for b in range(0, 20):
    s = [r["opposition"] for r in rows if b <= r["t"] < b + 1]
    if not s:
        print("  %2d-%2ds   (none)" % (b, b + 1))
        continue
    s.sort()
    print("  %2d-%2ds  n=%3d  %7.1f .. %7.1f   median %7.1f"
          % (b, b + 1, len(s), s[0], s[-1], s[len(s) // 2]))

hi = [r for r in rows if r["opposition"] > 100]
print("\nframes with opposition > 100: %d" % len(hi))
if hi:
    ts = sorted(r["t"] for r in hi)
    print("  they occur between t=%.1fs and t=%.1fs" % (ts[0], ts[-1]))
    fl = sorted(r["flexion"] for r in hi)
    print("  their flexion: %.1f .. %.1f (median %.1f)"
          % (fl[0], fl[-1], fl[len(fl) // 2]))
lo = [r for r in rows if r["opposition"] < -10]
print("frames with opposition < -10: %d" % len(lo))
if lo:
    ts = sorted(r["t"] for r in lo)
    print("  they occur between t=%.1fs and t=%.1fs" % (ts[0], ts[-1]))
