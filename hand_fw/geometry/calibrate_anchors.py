"""calibrate_anchors - can any parameter set separate the anchors?

The empirical interlock gives 15 anchor poses with known truth
(allowed / clashes). This searches the model's most-uncertain
parameters - finger MCP/PIP split, thumb split, thumb bend sweep
scale, shrink fraction - for sets that classify every anchor
correctly (BAD poses intersect the shrunk thumb, ALLOWED poses do
not). If none exists the geometry table has no basis to ship.

Usage: venv-geo/bin/python calibrate_anchors.py <links.yaml> <mesh_dir>
       <cylinders.json>
"""

import sys

import fcl
import numpy as np

import sample_collisions as sc
from sample_collisions import _init, _set_tf, _dist, _OBJ, _bvh

# (truth, (f, tb, rot)) - from the on-hand observations behind
# hand_safety.c: both-under-600 clashes (STA=5); one-side-at-600 and
# open-hand poses are in daily teleop use
ANCHORS = [
    ("ALLOWED", (600, 0, 1200)), ("ALLOWED", (600, 0, 2000)),
    ("ALLOWED", (0, 600, 1200)), ("ALLOWED", (0, 600, 2000)),
    ("ALLOWED", (800, 0, 1200)), ("ALLOWED", (2000, 0, 1200)),
    ("ALLOWED", (2000, 0, 2000)), ("ALLOWED", (0, 2000, 1200)),
    ("ALLOWED", (600, 600, 1200)), ("ALLOWED", (2000, 2000, 2000)),
    ("BAD", (0, 0, 1200)), ("BAD", (0, 0, 2000)),
    ("BAD", (300, 300, 1200)), ("BAD", (0, 0, 0)),
    ("BAD", (400, 300, 1500)),
]

PAIRS8 = [(th, ob) for th in ("sh.base", "sh.tip")
          for ob in ("index.prox", "index.tip",
                     "middle.prox", "middle.tip")]


def shrunk(mesh, frac):
    m = mesh.copy()
    c = m.centroid
    m.vertices = c + frac * (m.vertices - c)
    return fcl.CollisionObject(_bvh(m))


def clearance(H, f, tb, rot, cf, ct):
    for name in ("index", "middle"):
        Tp, Tt = H.finger_transforms(name, f, couple=cf)
        _set_tf(_OBJ[name + ".prox"], Tp)
        _set_tf(_OBJ[name + ".tip"], Tt)
    Tb, Tt = H.thumb_transforms(tb, rot, couple=ct)
    _set_tf(_OBJ["sh.base"], Tb)
    _set_tf(_OBJ["sh.tip"], Tt)
    d = 1e9
    for th, ob in PAIRS8:
        d = min(d, _dist(th, ob))
        if d <= 0.0:
            return d
    return d


def main(links_yaml, mesh_dir, cyl_json):
    _init(links_yaml, mesh_dir, cyl_json)
    H = sc._H
    sweep0 = H.thumb_bend_sweep
    results = []
    for frac in (0.92, 0.88, 0.84, 0.80):
        _OBJ["sh.base"] = shrunk(H.thumb_base, frac)
        _OBJ["sh.tip"] = shrunk(H.thumb_tipm, frac)
        for split_f in (0.3, 0.4, 0.5, 0.6, 0.7):    # MCP share of sweep
            cf = (1.0 - split_f) / split_f
            for split_t in (0.3, 0.5, 0.7):
                ct = (1.0 - split_t) / split_t
                for ksweep in (0.7, 0.85, 1.0, 1.15, 1.3):
                    H.thumb_bend_sweep = sweep0 * ksweep
                    ok = True
                    miss = []
                    margin_a = 1e9   # smallest ALLOWED clearance
                    margin_b = 1e9
                    for truth, (f, tb, rot) in ANCHORS:
                        d = clearance(H, f, tb, rot, cf, ct)
                        if truth == "ALLOWED":
                            margin_a = min(margin_a, d)
                            if d <= 0.0:
                                ok = False
                                miss.append(("A", f, tb, rot))
                        else:
                            margin_b = min(margin_b, d)
                            if d > 0.0:
                                ok = False
                                miss.append(("B", f, tb, rot))
                    results.append((ok, len(miss), frac, split_f, split_t,
                                    ksweep, round(margin_a, 2)))
                    if ok:
                        print("SEPARATES: shrink=%.2f split_f=%.1f "
                              "split_t=%.1f ksweep=%.2f  "
                              "min_allowed_clearance=%.2f"
                              % (frac, split_f, split_t, ksweep, margin_a))
    H.thumb_bend_sweep = sweep0
    if not any(r[0] for r in results):
        results.sort(key=lambda r: r[1])
        print("NO separating set. Best (fewest misses):")
        for r in results[:8]:
            print("  misses=%d shrink=%.2f split_f=%.1f split_t=%.1f "
                  "ksweep=%.2f" % (r[1], r[2], r[3], r[4], r[5]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
