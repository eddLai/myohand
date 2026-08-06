# geometry - STEP -> hand_collision_table.h

Offline pipeline that turns the vendor right-hand STEP model into the
thumb-vs-finger minimum-target tables hand_safety.c includes. Nothing
here runs at control time.

Pipeline (all on this machine, venv-geo):

1. `extract_step.py` - XCAF dump of the assembly (names are GBK),
   world bbox / volume / centre per instance -> assembly_inventory.json
2. `tessellate.py` - per-instance STLs + every cylindrical face
   (hinge pins) -> cylinders.json
3. `links.yaml` - instance -> link groups, hinge axes fitted from the
   pin cylinders, target->angle mapping, and the anchor calibration
4. `calibrate_anchors.py` - fits the uncertain parameters (MCP/PIP
   split, thumb split, thumb sweep scale, shrink) against 15 anchor
   poses with known on-hand truth; the ONLY separating set found is
   recorded in links.yaml
5. `sample_collisions.py` - shrunk-shell FCL distance over the 41^3
   (f, thumb_bend, thumb_rot) grid -> collision_grid.npz
6. `build_tables.py` - grid -> two 41x41 minimum-target tables +
   gates (clash anchors covered, allowed anchors untouched, all-open
   clean) -> ../hand_collision_table.h. Refuses to emit if a gate
   fails.

Model caveats, in honesty order:

- The thumb is ONE rigid solid in the CAD; bend articulation comes
  from splitting it at the distal-knuckle plane. Its hinge origins and
  the 30/70 joint splits come from the anchor fit, not from vendor
  kinematics.
- Interference is NOT monotone in finger curl (half-curled fingertips
  sit in the thumb sweep band), hence the 2x2 neighbour-max lookup and
  no smoothing.
- Allowed-anchor clearance is millimetres. The empirical scalar rules
  in hand_safety.c stay as the floor until an on-hand boundary replay
  (force<=300, speed<=400, poses stepped one cell outside the table
  boundary, JSONL-logged) confirms the tables alone.

Scratch inputs (STEP, STLs) live under /tmp/rh56f1_geo - re-extract
from ../仿人五指灵巧手-右.rar if the machine was cleaned.
