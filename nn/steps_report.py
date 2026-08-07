"""Re-run the thumb_steps verdict from the CSV, so the answer survives a
scrolled-away terminal. Same logic as the recorder's own report.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "thumb_steps.csv"))))
CH = ("flexion", "abduction", "opposition")
DRIVEN = {"A": "flexion", "B": "opposition"}
TITLE = {"A": "thumb BEND (steps 1-4)", "B": "thumb SWEEP / opposition (5-8)"}
TAG = {1: "A1", 2: "A2", 3: "A3", 4: "A4", 5: "B1", 6: "B2", 7: "B3", 8: "B4"}


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


for block in ("A", "B"):
    steps = sorted({int(r["step"]) for r in rows if r["block"] == block})
    driven = DRIVEN[block]
    print("\n########  BLOCK %s -- %s  ########" % (block, TITLE[block]))
    for model in ("lite", "full"):
        per = {s: {k: [float(r[k]) for r in rows if int(r["step"]) == s
                       and r["model"] == model and r["trust"] == "1"]
                   for k in CH} for s in steps}
        empty = [TAG[s] for s in steps if len(per[s][driven]) < 3]
        if empty:
            print("\n=== %s === %s had under 3 trusted frames -- cannot judge"
                  % (model, empty))
            continue
        print("\n=== %s === driven channel: %s" % (model, driven))
        print("  %-5s %5s %8s %8s %8s   %s"
              % ("step", "n", "p25", "median", "p75", "spread"))
        for s in steps:
            v = per[s][driven]
            print("  %-5s %5d %8.1f %8.1f %8.1f   %.1f .. %.1f"
                  % (TAG[s], len(v), pct(v, 25), pct(v, 50), pct(v, 75),
                     min(v), max(v)))
        med = [pct(per[s][driven], 50) for s in steps]
        up = all(b > a for a, b in zip(med, med[1:]))
        down = all(b < a for a, b in zip(med, med[1:]))
        clear = True
        touching = []
        for j in range(len(steps) - 1):
            a, b = per[steps[j]][driven], per[steps[j + 1]][driven]
            ok = (pct(a, 75) < pct(b, 25)) if med[j + 1] > med[j] \
                else (pct(a, 25) > pct(b, 75))
            clear &= ok
            if not ok:
                touching.append("%s|%s" % (TAG[steps[j]], TAG[steps[j + 1]]))
        verdict = ("SEPARATED" if (up or down) and clear else
                   "ORDERED, spreads touch" if (up or down) else "MUDDLED")
        print("  --> %s   medians %s   swing %.1f"
              % (verdict, " -> ".join("%.1f" % m for m in med),
                 max(med) - min(med)))
        if touching:
            print("      overlapping pairs: %s" % ", ".join(touching))
        for k in CH:
            if k == driven:
                continue
            m = [pct(per[s][k], 50) for s in steps]
            print("      leakage %-11s %s   swing %.1f"
                  % (k, " -> ".join("%.1f" % x for x in m), max(m) - min(m)))
