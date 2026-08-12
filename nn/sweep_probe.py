"""Hold a straight thumb at eight sweep angles, and watch flexion invent itself.

The thumb does not change shape when it rotates, so a straight thumb held at
any sweep angle should report the same flexion at all of them. It does not.
Past the point where the palm hides the thumb the net fills the gap from its
prior, landmarks 2 and 3 slide apart, and the angle between them - which is
what flexion measures - grows out of nothing.

The correction for that needs two numbers: where the palm starts hiding the
thumb, and how fast the invention grows after. Six points from two old
recordings put them near 70 degrees and 1.5, but only two of those points sit
past the edge, which is not enough to tell a line from a curve. This records
eight, half of them past the edge, in one pass.

Asking for an angle rather than a gesture is the point. "Touch your ring
finger" lands wherever that operator's ring finger happens to be; the earlier
recording swept 26 degrees one day and 116 the next while the instruction on
screen never changed. Here the bar shows the angle the model is reading right
now and the angle to hold, so the range is spanned on purpose instead of by
luck.

Targets are in the lite model's degrees because teleop runs lite and the
correction will be applied to lite's readings. The full model reads the same
sweep about a third wider and is recorded alongside for comparison, not used.

  python3 sweep_probe.py [device] [--frames=40] [--tol=6]

  s   skip the current angle if the hand will not reach it
  q   abort; whatever was recorded is still written and analysed
"""

import csv
import math
import os
import sys
import time

import cv2
import mediapipe as mp
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "camera"))
sys.path.insert(0, HERE)
import hand_mapping as hm  # noqa: E402
from calib_draw import (BAD, DIM, GREY, HI, OK, WHITE, draw_cjk)  # noqa: E402

opts = {a.split("=")[0]: a.split("=", 1)[1]
        for a in sys.argv[1:] if a.startswith("--") and "=" in a}
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
DEV = int(pos[0]) if pos else 0
NEED = int(opts.get("--frames", 40))
TOL = float(opts.get("--tol", 6))

# Dense either side of 70, where the two old recordings put the edge, and
# sparse below it where flexion was flat across 53 degrees of sweep. Spending
# frames where nothing happens buys nothing.
TARGETS = (5, 20, 35, 50, 62, 72, 82, 92)

OUT = os.path.join(HERE, "sweep_probe.csv")
OUT_LM = os.path.join(HERE, "sweep_probe_landmarks.csv")
WIN = "thumb sweep probe"
PANEL = 330
CAP_W, CAP_H = 1280, 720
FONT = cv2.FONT_HERSHEY_SIMPLEX
MODELS = (0, 1)
NAME = {0: "lite", 1: "full"}
CPLX = 0                      # the model teleop runs, and so the one that leads
SCALE_MAX = 130.0             # the sweep the full model sees; bar range only

draw = mp.solutions.drawing_utils
styles = mp.solutions.drawing_styles
nets = {c: mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=1,
                                    model_complexity=c,
                                    min_detection_confidence=0.5,
                                    min_tracking_confidence=0.5)
        for c in MODELS}


def measure(rgb):
    out = {}
    for c in MODELS:
        res = nets[c].process(rgb)
        if not res.multi_hand_world_landmarks:
            out[c] = None
            continue
        lbl = res.multi_handedness[0].classification[0]
        trust, why = hm.thumb_trust(res.multi_hand_landmarks[0].landmark,
                                    lbl.label, lbl.score, hm.HANDEDNESS)
        wl = res.multi_hand_world_landmarks[0].landmark
        out[c] = (res, int(trust), why, hm.thumb_features(wl, hm.HANDEDNESS),
                  wl, lbl.score)
    return out


def render(frame, res, idx, target, live, inband, got, trust, why, fe):
    accent = OK if inband else HI
    if not trust:
        accent = BAD
    if res is not None and res.multi_hand_landmarks:
        draw.draw_landmarks(frame, res.multi_hand_landmarks[0],
                            mp.solutions.hands.HAND_CONNECTIONS,
                            styles.get_default_hand_landmarks_style(),
                            styles.get_default_hand_connections_style())
    h, w = frame.shape[:2]
    canvas = np.zeros((h, w + PANEL, 3), np.uint8)
    canvas[:, :w] = frame
    band = canvas[:118, :w].copy()
    canvas[:118, :w] = cv2.addWeighted(band, 0.25, np.zeros_like(band), 0, 0)

    cv2.putText(canvas, "%d/%d" % (idx + 1, len(TARGETS)), (16, 40), FONT,
                0.7, GREY, 1, cv2.LINE_AA)
    cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), accent, 6)

    texts = [("轉到 %d 度" % target, (16, 52), 48, accent),
             ("拇指打直，只轉不折。轉到綠色為止，然後停住",
              (16, 108), 26, GREY)]

    # the angle bar: where the thumb is, and where it has to be. Reading a
    # number off a panel while holding a pose is harder than hitting a mark.
    bx, by, bw, bh = 16, h - 96, w - 32, 34
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (38, 38, 38), -1)
    for t in TARGETS:
        tx = bx + int(bw * t / SCALE_MAX)
        cv2.line(canvas, (tx, by + bh - 7), (tx, by + bh), DIM, 1)
    lo = bx + int(bw * max(0.0, target - TOL) / SCALE_MAX)
    hi = bx + int(bw * min(SCALE_MAX, target + TOL) / SCALE_MAX)
    cv2.rectangle(canvas, (lo, by), (hi, by + bh), (0, 90, 45), -1)
    if live is not None:
        lx = bx + int(bw * max(0.0, min(SCALE_MAX, live)) / SCALE_MAX)
        cv2.rectangle(canvas, (lx - 3, by - 6), (lx + 3, by + bh + 6), accent, -1)
    cv2.putText(canvas, "0", (bx, by + bh + 20), FONT, 0.45, GREY, 1, cv2.LINE_AA)
    cv2.putText(canvas, "%d" % SCALE_MAX, (bx + bw - 24, by + bh + 20), FONT,
                0.45, GREY, 1, cv2.LINE_AA)

    # progress is frames banked, not seconds elapsed: drifting out of the
    # band pauses the count instead of throwing away the hold so far
    done = got / float(NEED)
    cv2.rectangle(canvas, (0, h - 12), (w, h), (40, 40, 40), -1)
    cv2.rectangle(canvas, (0, h - 12), (int(w * min(1.0, done)), h), accent, -1)

    x, y = w + 18, 60
    if live is None:
        texts.append(("沒看到手", (x, y), 30, BAD))
    else:
        texts.append(("現在 %.0f 度" % live, (x, y), 34, WHITE))
        gap = target - live
        msg = "剛好，停住" if abs(gap) <= TOL else (
            "再轉 %.0f 度" % gap if gap > 0 else "轉回來 %.0f 度" % -gap)
        texts.append((msg, (x, y + 44), 28, accent))
        texts.append(("已錄 %d / %d 幀" % (got, NEED), (x, y + 92), 24, GREY))
        if fe is not None:
            texts.append(("彎曲讀數 %.0f" % fe["flexion"], (x, y + 130), 24, GREY))
        if not trust:
            texts.append(("不可信：%s" % why, (x, y + 168), 22, BAD))
    texts.append(("s 跳過   q 中止", (x, y + 230), 20, DIM))
    cv2.imshow(WIN, draw_cjk(canvas, texts))
    return cv2.waitKey(1) & 0xFF


cap = cv2.VideoCapture(DEV)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_H)
if not cap.isOpened():
    sys.exit("cannot open camera %d" % DEV)
cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)

print("八個掃描角度，每個要 %d 幀。%s手、手掌朝相機、拇指全程打直。"
      % (NEED, "右" if hm.HANDEDNESS == "Right" else "左"))
print("視窗上會顯示現在幾度、還要轉幾度。轉進綠色區間就會自動開始錄。\n")

rows, lm_rows, skipped = [], [], []
aborted = False
for idx, target in enumerate(TARGETS):
    got = 0
    while got < NEED:
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        m = measure(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        lead = m[CPLX]
        live = lead[3]["opposition"] if lead else None
        inband = live is not None and abs(live - target) <= TOL and lead[1]

        if inband:
            for c in MODELS:
                if m[c] is None:
                    continue
                _res, trust, why, fe, wl, score = m[c]
                r = {"target": target, "model": NAME[c], "trust": trust,
                     "why": why, "score": round(score, 3)}
                for k in ("flexion", "abduction", "opposition"):
                    r[k] = round(fe[k], 2)
                rows.append(r)
                lr = {"target": target, "model": NAME[c], "trust": trust}
                for j, p in enumerate(wl):
                    lr["x%d" % j] = round(p.x, 6)
                    lr["y%d" % j] = round(p.y, 6)
                    lr["z%d" % j] = round(p.z, 6)
                lm_rows.append(lr)
            got += 1

        key = render(frame, lead[0] if lead else None, idx, target, live,
                     inband, got, lead[1] if lead else 1,
                     lead[2] if lead else "", lead[3] if lead else None)
        if key in (ord("q"), 27):
            aborted = True
            break
        if key == ord("s"):
            skipped.append(target)
            print("  跳過 %d 度（錄到 %d 幀）" % (target, got))
            break
    else:
        print("  %d 度 完成" % target)
    if aborted:
        break

cap.release()
cv2.destroyAllWindows()
for _ in range(10):
    cv2.waitKey(30)

if aborted:
    print("\n（中止 — 已錄到的仍會分析）")
if skipped:
    print("跳過的角度：%s" % ", ".join(str(s) for s in skipped))
if not rows:
    sys.exit("沒有錄到任何資料")

for path, data in ((OUT, rows), (OUT_LM, lm_rows)):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)
    print("%d 列 -> %s" % (len(data), path))


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    return float("nan") if not n else (
        xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0)


def at(model, target, chan):
    return [r[chan] for r in rows
            if r["model"] == model and r["target"] == target and r["trust"]]


print("\n########  拇指打直，掃到不同角度時彎曲讀數是多少  ########")
print("（拇指沒變形，所以這一欄本來應該是常數）")
for model in (NAME[c] for c in MODELS):
    seen = sorted({r["target"] for r in rows if r["model"] == model})
    print("\n=== %s%s ===" % (model, "   <- teleop 跑這個" if model == NAME[CPLX] else ""))
    print("  %8s %8s %10s %6s" % ("目標", "實際對掌", "彎曲", "n"))
    for t in seen:
        o, f = at(model, t, "opposition"), at(model, t, "flexion")
        if len(f) < 5:
            print("  %8d %8s %10s %6d   可用幀不足" % (t, "-", "-", len(f)))
            continue
        print("  %8d %8.1f %10.1f %6d" % (t, med(o), med(f), len(f)))


def fit(model):
    """Grid the onset, least-squares the slope, keep the pair that leaves the
    flattest line. Fitting both at once would let a steep slope buy a wrong
    onset, and there are too few points to referee that."""
    pts = []
    for t in sorted({r["target"] for r in rows if r["model"] == model}):
        f, o = at(model, t, "flexion"), at(model, t, "opposition")
        if len(f) >= 5:
            pts.append((med(o), med(f)))
    if len(pts) < 4:
        return None
    best = None
    for onset in range(0, 111, 2):
        below = [f for o, f in pts if o <= onset]
        above = [(o, f) for o, f in pts if o > onset]
        if len(below) < 1 or len(above) < 2:
            continue
        base = med(below)
        num = sum((f - base) * (o - onset) for o, f in above)
        den = sum((o - onset) ** 2 for o, f in above)
        if den < 1e-9:
            continue
        k = num / den
        res = math.sqrt(sum((f - base - k * max(0.0, o - onset)) ** 2
                            for o, f in pts) / len(pts))
        if k > 0 and (best is None or res < best[0]):
            best = (res, onset, k, base, len(pts))
    return best


print("\n########  配出來的修正參數  ########")
for model in (NAME[c] for c in MODELS):
    b = fit(model)
    if b is None:
        print("  %-5s 點數不足，配不出來" % model)
        continue
    res, onset, k, base, n = b
    print("  %-5s OPP_ONSET %5.1f   OPP_LEAK %5.3f   殘差 %4.1f°   "
          "打直時的彎曲 %.1f   (%d 個角度)" % (model, onset, k, res, base, n))
print("\n殘差要跟「同一個姿勢定住不動時的抖動」比才有意義；"
      "之前那份是 15 度左右。")
print("要套用就把 OPP_ONSET / OPP_LEAK 寫進 calibration.json 的 windows。")
