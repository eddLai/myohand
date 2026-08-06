"""Sanity-check the kinematic model before the full scan."""
import sys
import time

import numpy as np

import sample_collisions as sc
from sample_collisions import _init, _set_tf, _dist, _OBJ, PAIRS


def clearance(f, tb, rot, couple=1.0, off=0.0, verbose=False):
    H = sc._H
    for name in ("index", "middle"):
        Tp, Tt = H.finger_transforms(name, f, couple, off)
        _set_tf(_OBJ[name + ".prox"], Tp)
        _set_tf(_OBJ[name + ".tip"], Tt)
    Tb, Tt = H.thumb_transforms(tb, rot, couple, off)
    _set_tf(_OBJ["thumb.base"], Tb)
    _set_tf(_OBJ["thumb.tip"], Tt)
    d = np.inf
    for th, ob in PAIRS:
        dd = _dist(th, ob)
        if verbose:
            print("    %-11s x %-11s %7.2f" % (th, ob, dd))
        d = min(d, dd)
    return d


t0 = time.time()
_init(*sys.argv[1:4])
H = sc._H
print("load %.1fs  signs=%s" % (time.time() - t0, H.signs))
print("mesh sizes: palm=%d idxp=%d thumb_base=%d thumb_tip=%d" % (
    len(H.palm.faces), len(H.fingers["index"]["prox"].faces),
    len(H.thumb_base.faces), len(H.thumb_tipm.faces)))
for name, f in H.fingers.items():
    tip_open = f["tip"].centroid
    prox, tip = H.posed_finger(name, 0)
    print("%-7s tip com open=%s closed=%s" % (
        name, np.round(tip_open, 1), np.round(tip.centroid, 1)))
tb_open = H.thumb_tipm.centroid
for s in (1.0, -1.0):
    H.signs["thumb_bend"] = s
    _, tipm = H.posed_thumb(0, 2000)
    print("thumb tip bend-only sign=%+d  open=%s closed=%s" % (
        s, np.round(tb_open, 1), np.round(tipm.centroid, 1)))
    _, tipm = H.posed_thumb(0, 0)
    print("thumb tip bend+rot  sign=%+d  closed=%s" % (
        s, np.round(tipm.centroid, 1)))
H._fix_signs()
_, tipm = H.posed_thumb(2000, 0)
print("thumb tip rot-only   open=%s rotated=%s" % (
    np.round(tb_open, 1), np.round(tipm.centroid, 1)))
print("per-pair clearance at all-open:")
clearance(2000, 2000, 2000, verbose=True)

t0 = time.time()
cases = [
    ("all open           ", 2000, 2000, 2000),
    ("obs. clash idx0 tb0", 0, 0, 1200),
    ("fist w/ rot safe   ", 0, 0, 2000),
    ("thumb sweep, idx cur", 400, 1500, 400),
    ("open fingers, tb 0 ", 2000, 0, 1000),
]
for label, f, tb, rot in cases:
    print("%s f=%4d tb=%4d rot=%4d  clearance=%7.2f mm" % (
        label, f, tb, rot, clearance(f, tb, rot)))
print("5 poses in %.1fs" % (time.time() - t0))
