"""Compare what the hand would actually be told to do, through both front ends.

Everything measured so far - landmark pixels, joint angles - sits upstream of
the number that reaches the motors. teleop takes the world landmarks, runs
pose_from_world_landmarks to get six targets in counts, and gates the thumb on
thumb_trust. Those two calls are the whole interface, so running them on both
front ends over the same recording says directly how differently the hand would
move, in the units the driver speaks.

The trust gate is reported separately because a disagreement there is not a
small error: it decides whether the thumb follows the operator or freezes.

    python3 compare_targets.py [refcap/frames.npz]
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/myohand-pipeline/camera"))
import hand_mapping as hm
import hand_pipeline as hp

NPZ = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/myohand/nn/refcap/frames.npz")
d = np.load(NPZ)
frames, found, world, label, score = (d["frames"], d["found"], d["world"],
                                      d["label"], d["score"])
H, W = frames.shape[1], frames.shape[2]

mine = hp.MediaPipeHands(W, H, threads=4)
rows, only_mp, only_mine, trust_split = [], 0, 0, 0
for i, bgr in enumerate(frames):
    r = mine.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    got = bool(r.multi_hand_world_landmarks)
    if got != bool(found[i]):
        only_mine += int(got)
        only_mp += int(not got)
        continue
    if not got:
        continue
    a = hm.pose_from_world_landmarks(r.multi_hand_world_landmarks[0].landmark)
    b = hm.pose_from_world_landmarks(
        [type("P", (), {"x": float(x), "y": float(y), "z": float(z)})()
         for x, y, z in world[i]])
    cls = r.multi_handedness[0].classification[0]
    ta, _ = hm.thumb_trust(r.multi_hand_landmarks[0].landmark, cls.label,
                           cls.score)
    tb = (str(label[i]) == hm.HANDEDNESS and float(score[i]) >= hm.LABEL_SURE)
    trust_split += int(ta != tb)
    rows.append((np.array(a, float), np.array(b, float)))

print("%d 幀可比對；只有我找到 %d，只有 MediaPipe 找到 %d"
      % (len(rows), only_mine, only_mp))
print("thumb_trust 判定不同 %d 幀（%.0f%%）\n"
      % (trust_split, 100.0 * trust_split / max(len(rows), 1)))

A = np.stack([r[0] for r in rows])
B = np.stack([r[1] for r in rows])
diff = np.abs(A - B)
names = ["小指", "無名指", "中指", "食指", "拇指彎曲", "拇指對掌"]
print("%-10s %8s %8s %8s   %s" % ("軸", "中位", "p95", "最大", "行程"))
for j, n in enumerate(names):
    span = B[:, j].max() - B[:, j].min()
    print("%-10s %7.0f  %7.0f  %7.0f   %5.0f counts"
          % (n, np.median(diff[:, j]), np.percentile(diff[:, j], 95),
             diff[:, j].max(), span))
print("\n（行程 = MediaPipe 在這段錄影裡實際用到的範圍，不是滿量程）")
