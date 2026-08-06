"""kinematics - pose the RH56F1 link meshes from 6-axis targets.

Loads links.yaml + the tessellated STLs and returns posed copies of the
moving meshes for any [pinky, ring, middle, index, thumb_bend,
thumb_rot] target vector (0..2000, 2000 = fully open = the CAD pose).

Rotation signs are not guessed: at import the curl direction of every
hinge is chosen so that closing (target -> 0) moves the fingertips
toward the palm face and the thumb toward the palm centre, then frozen.
"""

import numpy as np
import trimesh
import yaml

DEG = np.pi / 180.0


def _rot(axis, origin, ang):
    T = trimesh.transformations.rotation_matrix(ang, axis, origin)
    return T


def _hinge_from_cyls(cyls, low_y):
    """MCP (low_y) or PIP hinge of a finger part: the r~1.5 pin cluster."""
    pins = [c for c in cyls
            if 1.0 <= c["radius"] <= 2.5 and abs(c["axis"][0]) > 0.9]
    if not pins:
        raise ValueError("no hinge pins found")
    ys = sorted(set(round(c["origin"][1], 0) for c in pins))
    want = ys[0] if low_y else ys[-1]
    sel = [c for c in pins if abs(c["origin"][1] - want) < 3]
    o = np.mean([c["origin"] for c in sel], axis=0)
    return {"origin": o, "axis": np.array([1.0, 0.0, 0.0])}


class Hand:
    def __init__(self, links_yaml, mesh_dir, cyl_json):
        import json
        import os
        self.cfg = yaml.safe_load(open(links_yaml))
        cyls = json.load(open(cyl_json))
        load = lambda inst: trimesh.load(
            os.path.join(mesh_dir, inst + ".stl"), force="mesh")

        self.palm = trimesh.util.concatenate(
            [load(i) for i in self.cfg["palm"]["instances"]])

        self.fingers = {}
        for name, f in self.cfg["fingers"].items():
            if name == "sweep_deg":
                continue
            prox = load(f["proximal"])
            tip = load(f["tip"])
            self.fingers[name] = {
                "prox": prox, "tip": tip,
                "mcp": _hinge_from_cyls(cyls[f["proximal"]]["cylinders"], True),
                "pip": _hinge_from_cyls(cyls[f["proximal"]]["cylinders"], False),
            }
        self.finger_sweep = self.cfg["fingers"]["sweep_deg"] * DEG

        t = self.cfg["thumb"]
        thumb = load(t["instance"])
        self.thumb_bend = {k: np.array(t["bend_hinge"][k]) for k in t["bend_hinge"]}
        self.thumb_distal = {k: np.array(t["bend_distal"][k]) for k in t["bend_distal"]}
        self.thumb_rot = {k: np.array(t["rot_hinge"][k]) for k in t["rot_hinge"]}
        self.thumb_bend_sweep = t["bend_sweep_deg"] * DEG
        self.thumb_rot_sweep = t["rot_sweep_deg"] * DEG

        # split the rigid thumb at the plane through the distal knuckle,
        # normal along base->distal. The halves stay raw triangle soups:
        # FCL distance handles open shells, and hulls would fill the
        # bearing-mount concavity and sit inside the palm forever.
        long_dir = self.thumb_distal["origin"] - self.thumb_bend["origin"]
        long_dir = long_dir / np.linalg.norm(long_dir)
        d = thumb.vertices.dot(long_dir) - self.thumb_distal["origin"].dot(long_dir)
        below = d <= 1.0
        above = d >= -1.0
        base_faces = below[thumb.faces].all(axis=1)
        tip_faces = above[thumb.faces].all(axis=1)
        self.thumb_base = thumb.submesh([np.nonzero(base_faces)[0]],
                                        append=True)
        self.thumb_tipm = thumb.submesh([np.nonzero(tip_faces)[0]],
                                        append=True)

        self._fix_signs()

    # -- sign calibration ------------------------------------------------
    def _fix_signs(self):
        self.signs = {}
        palm_face_z = self.palm.bounds[0][2]      # palm pad side (low z)
        for name, f in self.fingers.items():
            tip0 = f["tip"].centroid
            best = None
            for s in (1.0, -1.0):
                T = _rot(s * f["mcp"]["axis"], f["mcp"]["origin"], 60 * DEG)
                z = trimesh.transform_points([tip0], T)[0][2]
                if best is None or z < best[1]:
                    best = (s, z)
            self.signs[name] = best[0]            # curl lowers the tip
        centre = np.array([-10.0, self.palm.centroid[1],
                           self.palm.centroid[2]])
        tip0 = self.thumb_tipm.centroid
        override = self.cfg["thumb"].get("rot_sign")
        if override:
            self.signs["thumb_rot"] = float(override)
        else:
            best = None
            for s in (1.0, -1.0):
                T = _rot(s * self.thumb_rot["axis"],
                         self.thumb_rot["origin"], 50 * DEG)
                p = trimesh.transform_points([tip0], T)[0]
                dist = np.linalg.norm(p - centre)
                if best is None or dist < best[1]:
                    best = (s, dist)
            self.signs["thumb_rot"] = best[0]     # rot sweeps toward palm
        override = self.cfg["thumb"].get("bend_sign")
        if override:
            self.signs["thumb_bend"] = float(override)
        else:
            best = None
            for s in (1.0, -1.0):
                T = _rot(s * self.thumb_bend["axis"],
                         self.thumb_bend["origin"], 40 * DEG)
                p = trimesh.transform_points([tip0], T)[0]
                score = np.linalg.norm(p[:2] - centre[:2]) \
                    + (p[2] - palm_face_z)
                if best is None or score < best[1]:
                    best = (s, score)
            self.signs["thumb_bend"] = best[0]    # bend folds toward palm

    # -- posing ----------------------------------------------------------
    def _closed_frac(self, target):
        return (2000.0 - np.clip(target, 0, 2000)) / 2000.0

    def finger_transforms(self, name, target, couple=1.0, off_deg=0.0):
        """(4x4 proximal, 4x4 tip) world transforms for one finger."""
        f = self.fingers[name]
        s = self.signs[name]
        total = self._closed_frac(target) * self.finger_sweep + off_deg * DEG
        total = max(0.0, total)
        mcp_ang = total / (1.0 + couple)
        pip_ang = total - mcp_ang
        Tm = _rot(s * f["mcp"]["axis"], f["mcp"]["origin"], mcp_ang)
        Tp = _rot(s * f["pip"]["axis"], f["pip"]["origin"], pip_ang)
        return Tm, Tm @ Tp

    def thumb_transforms(self, t_bend, t_rot, couple=1.0, off_deg=0.0):
        """(4x4 base half, 4x4 tip half) world transforms for the thumb."""
        rot_ang = self.signs["thumb_rot"] * (
            self._closed_frac(t_rot) * self.thumb_rot_sweep)
        Tr = _rot(self.thumb_rot["axis"], self.thumb_rot["origin"], rot_ang)
        total = self._closed_frac(t_bend) * self.thumb_bend_sweep \
            + off_deg * DEG
        total = max(0.0, total)
        base_ang = self.signs["thumb_bend"] * total / (1.0 + couple)
        dist_ang = self.signs["thumb_bend"] * total * couple / (1.0 + couple)
        Tb = _rot(self.thumb_bend["axis"], self.thumb_bend["origin"],
                  base_ang)
        Td = _rot(self.thumb_distal["axis"], self.thumb_distal["origin"],
                  dist_ang)
        return Tr @ Tb, Tr @ Tb @ Td

    def posed_finger(self, name, target, couple=1.0, off_deg=0.0):
        Tm, Tt = self.finger_transforms(name, target, couple, off_deg)
        f = self.fingers[name]
        prox = f["prox"].copy()
        prox.apply_transform(Tm)
        tip = f["tip"].copy()
        tip.apply_transform(Tt)
        return prox, tip

    def posed_thumb(self, t_bend, t_rot, couple=1.0, off_deg=0.0):
        Tb, Tt = self.thumb_transforms(t_bend, t_rot, couple, off_deg)
        base = self.thumb_base.copy()
        base.apply_transform(Tb)
        tipm = self.thumb_tipm.copy()
        tipm.apply_transform(Tt)
        return base, tipm
