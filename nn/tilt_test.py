#!/usr/bin/env python3
"""Does turning the hand let the camera see the thumb reach the palm?

With the palm square to the camera, "how far is the thumb from the palm"
points straight down the line of sight -- the one direction a single
camera cannot measure. MediaPipe has to guess it, and its guess is a
thumb held clear of the palm: pressed flat, the tip still reads at half
a hand-length above the palm plane, so bend saturates and then reverses.

Turn the hand and that gap becomes a sideways distance in the image.
This records the same three thumb positions twice, square and turned,
in one session so lighting and hand are identical.

The number that decides it is tip height above the palm plane, scaled by
hand length. Pressed flat it should approach zero. Whichever orientation
gets closer to zero is the one the camera can actually see.

thumb_trust rejects an edge-on hand (FACING_MARGIN), which a turned hand
may trip. Every frame is therefore kept with BOTH verdicts -- the shipped
threshold and a relaxed one -- so the gate can be judged separately from
the measurement. The relaxed value is set on the imported module in this
process only; hand_mapping.py on disk is not touched.

    ../venv/bin/python3 tilt_test.py [device] [seconds_per_pose]
"""
import csv
import math
import os
import sys
import time

import cv2
import numpy as np
import mediapipe as mp
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

DEV = int(sys.argv[1]) if len(sys.argv) > 1 else 0
HOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
READY = 4.0
RELAXED = 0.03                      # vs the shipped FACING_MARGIN = 0.10
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tilt_steps.csv")
OUT_LM = os.path.join(HERE, "tilt_landmarks.csv")

STEPS = [
    ("S1", "手掌正對鏡頭 · 拇指打直", "square"),
    ("S2", "手掌正對鏡頭 · 拇指彎 2/3", "square"),
    ("S3", "手掌正對鏡頭 · 拇指壓平貼掌", "square"),
    ("T1", "手掌側轉 45 度 · 拇指打直", "tilt"),
    ("T2", "手掌側轉 45 度 · 拇指彎 2/3", "tilt"),
    ("T3", "手掌側轉 45 度 · 拇指壓平貼掌", "tilt"),
]
HINT = {"square": "手掌面對鏡頭，像平常那樣",
        "tilt": "手掌轉 45 度，讓鏡頭從側面看到拇指和手掌之間"}
PALM = (0, 5, 9, 13, 17)
MODELS = (0, 1)
NAME = {0: "lite", 1: "full"}
WIN = "tilt test"
PANEL = 330
FONT = cv2.FONT_HERSHEY_SIMPLEX
CJK_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
WHITE, GREY, DIM = (240, 240, 240), (155, 155, 155), (70, 70, 70)
OK, BAD, HI = (90, 220, 120), (80, 80, 245), (255, 175, 55)
_fonts = {}


def cjk_font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(CJK_PATH, size)
    return _fonts[size]


def draw_cjk(img, items):
    if not items:
        return img
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, (x, y), size, col in items:
        d.text((x, y), text, font=cjk_font(size), fill=(col[2], col[1], col[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


def tip_height(wl):
    """Thumb tip above the palm plane, as a percentage of hand length.

    This is the falsifiable number: with the thumb flat on the palm it is
    physically near zero, whatever the camera thinks.
    """
    p = np.array([[q.x, q.y, q.z] for q in wl])
    a = p[5] - p[0]
    n = np.cross(a, p[17] - p[0])
    m = np.linalg.norm(n)
    hand = np.linalg.norm(p[9] - p[0])
    if m < 1e-9 or hand < 1e-9:
        return None
    n = n / m
    c = p[list(PALM)].mean(axis=0)
    return 100.0 * abs(float(np.dot(p[4] - c, n))) / hand


draw = mp.solutions.drawing_utils
styles = mp.solutions.drawing_styles
nets = {c: mp.solutions.hands.Hands(max_num_hands=1, model_complexity=c,
                                    min_detection_confidence=0.6,
                                    min_tracking_confidence=0.5)
        for c in MODELS}
cap = cv2.VideoCapture(DEV)
if not cap.isOpened():
    sys.exit("cannot open camera %d" % DEV)
cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
SHIPPED = hm.FACING_MARGIN


def both_verdicts(lm, label, score):
    hm.FACING_MARGIN = SHIPPED
    std = hm.thumb_trust(lm, label, score, hm.HANDEDNESS)
    hm.FACING_MARGIN = RELAXED
    rel = hm.thumb_trust(lm, label, score, hm.HANDEDNESS)
    hm.FACING_MARGIN = SHIPPED
    return std, rel


def render(frame, res, i, phase, left, span, std, rel, tf, hgt, got):
    tag, zh, kind = STEPS[i]
    holding = phase == "HOLD"
    accent = OK if holding else HI
    if res is not None and res.multi_hand_landmarks:
        draw.draw_landmarks(frame, res.multi_hand_landmarks[0],
                            mp.solutions.hands.HAND_CONNECTIONS,
                            styles.get_default_hand_landmarks_style(),
                            styles.get_default_hand_connections_style())
    h, w = frame.shape[:2]
    canvas = np.zeros((h, w + PANEL, 3), np.uint8)
    canvas[:, :w] = frame
    band = canvas[:96, :w].copy()
    canvas[:96, :w] = cv2.addWeighted(band, 0.25, np.zeros_like(band), 0, 0)
    cv2.putText(canvas, tag, (16, 62), FONT, 1.9, accent, 4, cv2.LINE_AA)
    cv2.putText(canvas, "%d/%d" % (i + 1, len(STEPS)), (16, 84), FONT, 0.5,
                GREY, 1, cv2.LINE_AA)
    done = 0.0 if span <= 0 else max(0.0, min(1.0, 1.0 - left / span))
    cv2.rectangle(canvas, (0, h - 12), (w, h), (40, 40, 40), -1)
    cv2.rectangle(canvas, (0, h - 12), (int(w * done), h), accent, -1)
    cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), accent, 6)

    texts = [(zh, (108, 8), 34, WHITE)]
    if holding:
        texts.append(("保持不動  %.1f 秒" % left, (108, 52), 28, OK))
    else:
        texts.append(("準備… %d" % int(math.ceil(left)), (108, 52), 28, HI))
        texts.append((HINT[kind], (16, h - 56), 24, GREY))

    x, y = w + 14, 34

    def line(t, col=WHITE, sc=0.5, dy=22, th=1):
        nonlocal y
        cv2.putText(canvas, t, (x, y), FONT, sc, col, th, cv2.LINE_AA)
        y += dy

    if std is None:
        line("NO HAND", BAD, 0.8, 30, 2)
    else:
        line("shipped  %s" % ("OK" if std[0] else "REJ"),
             OK if std[0] else BAD, 0.6, 24, 2)
        line("relaxed  %s" % ("OK" if rel[0] else "REJ"),
             OK if rel[0] else BAD, 0.6, 26, 2)
        if not rel[0]:
            texts.append((rel[1] or "?", (x, y - 18), 19, BAD))
            y += 16
    cv2.line(canvas, (x, y - 8), (w + PANEL - 14, y - 8), DIM, 1)
    y += 6
    line("kept: ship %d / relax %d" % (got[0], got[1]), GREY, 0.45)
    cv2.line(canvas, (x, y - 8), (w + PANEL - 14, y - 8), DIM, 1)
    y += 6
    if hgt is not None:
        col = OK if hgt < 20 else BAD
        line("tip above palm", GREY, 0.45, 20)
        line("%.0f %%" % hgt, col, 1.3, 42, 3)
        line("flat on palm -> near 0", GREY, 0.38, 24)
    if tf:
        for k in ("flexion", "abduction", "opposition"):
            line("%-11s %7.1f" % (k, tf[k]), WHITE, 0.48)
    line("q / ESC to abort", GREY, 0.4)
    cv2.imshow(WIN, draw_cjk(canvas, texts))
    return cv2.waitKey(1) & 0xFF


print("六個姿勢：三個正對鏡頭、三個側轉 45 度，各 %.0f 秒。" % HOLD)
print("右手。指示顯示在視窗上，邊框變綠就是正在錄，此時手不要動。")
print("要看的數字是右邊那個大的百分比：拇指尖離手掌平面多高。")
print("壓平貼掌時它應該接近 0；哪個角度比較接近 0，就是鏡頭真的看得到的那個。\n")

rows, lm_rows, aborted = [], [], False
for i, (tag, zh, kind) in enumerate(STEPS):
    if aborted:
        break
    print("\n>>> %s  %s" % (tag, zh))
    got = [0, 0]
    for phase, span in (("READY", READY), ("HOLD", HOLD)):
        t0 = time.time()
        while True:
            left = span - (time.time() - t0)
            if left <= 0:
                break
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            shown = None
            for c in MODELS:
                res = nets[c].process(rgb)
                if not res.multi_hand_world_landmarks:
                    continue
                lbl = res.multi_handedness[0].classification[0]
                std, rel = both_verdicts(res.multi_hand_landmarks[0].landmark,
                                         lbl.label, lbl.score)
                wl = res.multi_hand_world_landmarks[0].landmark
                tf = hm.thumb_features(wl, hm.HANDEDNESS)
                hgt = tip_height(wl)
                if c == 1 or shown is None:
                    shown = (res, std, rel, tf, hgt)
                if phase != "HOLD":
                    continue
                if c == 1:
                    got[0] += int(std[0])
                    got[1] += int(rel[0])
                rows.append({"step": i + 1, "tag": tag, "kind": kind,
                             "model": NAME[c], "trust": int(std[0]),
                             "trust_relaxed": int(rel[0]), "why": rel[1],
                             "tip_height": None if hgt is None else round(hgt, 2),
                             "flexion": round(tf["flexion"], 2),
                             "abduction": round(tf["abduction"], 2),
                             "opposition": round(tf["opposition"], 2)})
                rec = {"step": i + 1, "tag": tag, "kind": kind,
                       "model": NAME[c], "trust": int(std[0]),
                       "trust_relaxed": int(rel[0])}
                for j, p in enumerate(wl):
                    rec["x%d" % j] = round(p.x, 6)
                    rec["y%d" % j] = round(p.y, 6)
                    rec["z%d" % j] = round(p.z, 6)
                lm_rows.append(rec)
            key = render(frame, shown[0] if shown else None, i, phase, left,
                         span, shown[1] if shown else None,
                         shown[2] if shown else None,
                         shown[3] if shown else None,
                         shown[4] if shown else None, got)
            if key in (ord("q"), 27):
                aborted = True
                break
        if aborted:
            break
    print("    保留幀數（full）: 出廠門檻 %d, 放寬門檻 %d" % (got[0], got[1]))

cap.release()
cv2.destroyAllWindows()
if not rows:
    sys.exit("沒有錄到任何資料")
for path, data in ((OUT, rows), (OUT_LM, lm_rows)):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)
print("\n%d 列 -> %s" % (len(rows), OUT))
print("%d 列原始 21 點 -> %s" % (len(lm_rows), OUT_LM))

for gate in ("trust", "trust_relaxed"):
    print("\n\n########  以 %s 篩選  ########"
          % ("出廠門檻 FACING_MARGIN=%.2f" % SHIPPED if gate == "trust"
             else "放寬門檻 FACING_MARGIN=%.2f" % RELAXED))
    for model in ("lite", "full"):
        print("\n=== %s ===" % model)
        print("  %-28s %6s %12s %10s" % ("姿勢", "n", "拇指尖離掌%", "flexion"))
        for i, (tag, zh, kind) in enumerate(STEPS):
            R = [r for r in rows if r["tag"] == tag and r["model"] == model
                 and r[gate] == 1 and r["tip_height"] not in (None, "")]
            if not R:
                print("  %-28s %6s %12s %10s" % (zh, 0, "-", "-"))
                continue
            hgt = [float(r["tip_height"]) for r in R]
            flx = [float(r["flexion"]) for r in R]
            print("  %-28s %6d %12.1f %10.1f"
                  % (zh, len(R), pct(hgt, 50), pct(flx, 50)))

print("\n\n怎麼判讀：")
print("  比較「拇指壓平貼掌」這兩列的『拇指尖離掌%』。")
print("  側轉 45 度那列明顯比較小 -> 轉手真的讓鏡頭看到了，值得繼續往這走。")
print("  兩列差不多            -> 轉手沒用，問題不是視線方向，得換感測。")
print("  另外看 flexion 在同一個角度下是否 打直 < 彎2/3 < 壓平 單調遞增。")
