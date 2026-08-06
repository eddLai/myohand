"""sample_collisions - shrunk-shell clearance over the (f, tb, rot) grid.

Poses the anchor-calibrated model (links.yaml `calibration`) at every
gridpoint of (f = index&middle curl, thumb_bend, thumb_rot) and records
the min FCL distance between the SHRUNK thumb shells and index/middle.
A cell is a deep-overlap (forbidden) candidate when the distance falls
under `forbid_below_mm`; build_tables.py turns that into the C tables.

Usage: venv-geo/bin/python sample_collisions.py <links.yaml> <mesh_dir>
       <cylinders.json> <out.npz>
"""

import sys

import fcl
import numpy as np
from multiprocessing import Pool

from kinematics import Hand

TARGETS = np.arange(0, 2001, 50)             # 41 gridpoints, HCT_STEP=50

PAIRS = [(th, ob) for th in ("thumb.base", "thumb.tip")
         for ob in ("index.prox", "index.tip",
                    "middle.prox", "middle.tip")]

_H = None
_OBJ = {}
_CAL = None


def _bvh(mesh):
    m = fcl.BVHModel()
    m.beginModel(len(mesh.vertices), len(mesh.faces))
    m.addSubModel(np.asarray(mesh.vertices, float),
                  np.asarray(mesh.faces, int))
    m.endModel()
    return m


def _shrunk(mesh, frac):
    m = mesh.copy()
    c = m.centroid
    m.vertices = c + frac * (m.vertices - c)
    return m


def _init(links_yaml, mesh_dir, cyl_json):
    global _H, _CAL
    _H = Hand(links_yaml, mesh_dir, cyl_json)
    _CAL = _H.cfg["calibration"]
    _H.thumb_bend_sweep *= _CAL["thumb_sweep_scale"]
    frac = _CAL["shrink"]
    for n in ("index", "middle"):
        _OBJ[n + ".prox"] = fcl.CollisionObject(_bvh(_H.fingers[n]["prox"]))
        _OBJ[n + ".tip"] = fcl.CollisionObject(_bvh(_H.fingers[n]["tip"]))
    _OBJ["thumb.base"] = fcl.CollisionObject(_bvh(_shrunk(_H.thumb_base,
                                                          frac)))
    _OBJ["thumb.tip"] = fcl.CollisionObject(_bvh(_shrunk(_H.thumb_tipm,
                                                         frac)))


def _couple(share):
    return (1.0 - share) / share


def _set_tf(obj, T):
    obj.setTransform(fcl.Transform(T[:3, :3], T[:3, 3]))


def _dist(a, b):
    req = fcl.DistanceRequest()
    res = fcl.DistanceResult()
    return fcl.distance(_OBJ[a], _OBJ[b], req, res)


def clearance(f, tb, rot):
    """Shrunk-shell min distance at one pose (calibrated model)."""
    cf = _couple(_CAL["finger_mcp_share"])
    ct = _couple(_CAL["thumb_base_share"])
    for name in ("index", "middle"):
        Tp, Tt = _H.finger_transforms(name, f, couple=cf)
        _set_tf(_OBJ[name + ".prox"], Tp)
        _set_tf(_OBJ[name + ".tip"], Tt)
    Tb, Tt = _H.thumb_transforms(tb, rot, couple=ct)
    _set_tf(_OBJ["thumb.base"], Tb)
    _set_tf(_OBJ["thumb.tip"], Tt)
    d = np.inf
    for th, ob in PAIRS:
        d = min(d, _dist(th, ob))
        if d <= 0.0:
            break
    return d


def _scan_f(fi):
    n = len(TARGETS)
    out = np.empty((n, n))
    for bi, tb in enumerate(TARGETS):
        for ri, rot in enumerate(TARGETS):
            out[bi, ri] = clearance(TARGETS[fi], tb, rot)
    return fi, out


def main(links_yaml, mesh_dir, cyl_json, out_npz, procs=17):
    n = len(TARGETS)
    dist = np.full((n, n, n), np.inf)
    with Pool(procs, initializer=_init,
              initargs=(links_yaml, mesh_dir, cyl_json)) as pool:
        for fi, sl in pool.imap_unordered(_scan_f, range(n)):
            dist[fi] = sl
            print("f=%d done, min=%.2f mm" % (TARGETS[fi], sl.min()),
                  flush=True)
    np.savez_compressed(out_npz, targets=TARGETS, dist=dist)
    print("saved", out_npz)


if __name__ == "__main__":
    main(*sys.argv[1:5])
