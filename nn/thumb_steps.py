#!/usr/bin/env python3
"""Can the camera tell one thumb position from another?

A window can be right and a channel steady while the measurement still
fails to separate poses a person would call obviously different. That is
the question here, asked separately for the two channels that matter:
the thumb's bend, and its sweep across the palm.

Two blocks of four held positions:

  A  bend      straight -> a third -> two thirds -> flat on the palm
  B  sweep     open -> index -> ring -> pinky        (drives opposition)

Every frame goes to BOTH models, so one recording answers the question
for the lite model teleop actually runs and for the full model at once.

THE POSE MUST BE HELD STILL for the whole HOLD phase. A first run failed
not because the camera could not measure -- it measured a resting thumb
to within 2 degrees -- but because the poses moved during the window, so
"spread inside a pose" was really the hand travelling. The window now
states the pose in Chinese at full size, counts down, and turns its
border green while recording, because the operator cannot be expected to
watch a terminal while posing.

Verdict per driven channel:
  SEPARATED   every step's middle half sits clear of its neighbours'
  ORDERED     medians climb in the right order but the spreads touch
  MUDDLED     the medians do not even order correctly

The two channels a block does not drive are reported as leakage: they
should have stayed put, and a large swing means the decomposition is
letting one motion bleed into another.

Camera only. Writes thumb_steps.csv and thumb_steps_landmarks.csv.
Nothing in the runtime chain is touched -- this is a ruler, not a part.

    ../venv/bin/python3 thumb_steps.py [device] [seconds_per_step]

    q or ESC aborts and still writes whatever was recorded.
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
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "thumb_steps.csv")
# The raw 21 points go to their own file. Angles are a lossy summary: once
# they are all that survives, any question about how they were computed --
# a different palm normal, a different decomposition -- needs the operator
# back in front of the camera. Landmarks cost one file and answer offline.
OUT_LM = os.path.join(HERE, "thumb_steps_landmarks.csv")

STEPS = [
    ("A1", "拇指打直，離開手掌", "A"),
    ("A2", "拇指彎約 1/3", "A"),
    ("A3", "拇指彎約 2/3", "A"),
    ("A4", "拇指壓平貼在手掌上", "A"),
    ("B1", "拇指張開，遠離其他手指", "B"),
    ("B2", "拇指碰食指根部", "B"),
    ("B3", "拇指碰無名指", "B"),
    ("B4", "拇指碰小指根部", "B"),
]
BLOCK_HINT = {"A": "四指張開別動，只動拇指的彎曲",
              "B": "拇指往小指方向掃過手掌"}
CH = ("flexion", "abduction", "opposition")
DRIVEN = {"A": "flexion", "B": "opposition"}
BLOCK_NAME = {"A": "thumb BEND", "B": "thumb SWEEP across the palm"}
AXES = ("pinky", "ring", "middle", "index", "thumbBend", "thumbRot")
MODELS = (0, 1)
NAME = {0: "lite", 1: "full"}

WIN = "thumb step test"
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
    """One PIL round trip for every Chinese string on this frame."""
    if not items:
        return img
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, (x, y), size, col in items:
        d.text((x, y), text, font=cjk_font(size),
               fill=(col[2], col[1], col[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


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


def render(frame, res, idx, phase, left, span, trust, why, tf, tgt, got):
    tag, zh, block = STEPS[idx]
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

    # a banner across the video, because the operator is looking at the hand
    band = canvas[:96, :w].copy()
    canvas[:96, :w] = cv2.addWeighted(band, 0.25, np.zeros_like(band), 0, 0)
    cv2.putText(canvas, tag, (16, 62), FONT, 1.9, accent, 4, cv2.LINE_AA)
    cv2.putText(canvas, "%d/%d" % (idx + 1, len(STEPS)), (16, 84), FONT,
                0.5, GREY, 1, cv2.LINE_AA)

    # the hold clock, as a bar the whole width of the video
    done = 0.0 if span <= 0 else max(0.0, min(1.0, 1.0 - left / span))
    cv2.rectangle(canvas, (0, h - 12), (w, h), (40, 40, 40), -1)
    cv2.rectangle(canvas, (0, h - 12), (int(w * done), h), accent, -1)
    cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), accent, 6)

    texts = [(zh, (108, 8), 40, WHITE)]
    if holding:
        texts.append(("保持不動  %.1f 秒" % left, (108, 56), 30, OK))
    else:
        texts.append(("準備… %d" % int(math.ceil(left)), (108, 56), 30, HI))
        texts.append((BLOCK_HINT[block], (16, h - 58), 26, GREY))

    x, y = w + 14, 32

    def line(text, col=WHITE, scale=0.5, dy=22, thick=1):
        nonlocal y
        cv2.putText(canvas, text, (x, y), FONT, scale, col, thick, cv2.LINE_AA)
        y += dy

    def rule():
        nonlocal y
        cv2.line(canvas, (x, y - 8), (w + PANEL - 14, y - 8), DIM, 1)
        y += 6

    if trust is None:
        line("NO HAND", BAD, 0.8, 30, 2)
    elif trust:
        line("TRUSTED", OK, 0.8, 30, 2)
    else:
        line("REJECTED", BAD, 0.8, 26, 2)
        texts.append((why or "?", (x, y - 16), 20, BAD))
        y += 18
    rule()
    line("usable this pose", GREY, 0.42, 20)
    line("lite %3d    full %3d" % (got[0], got[1]),
         BAD if not any(got.values()) else OK, 0.66, 28, 2)
    rule()

    if tf:
        for k in CH:
            col = HI if k == DRIVEN[block] else WHITE
            line("%-11s %7.1f" % (k, tf[k]), col, 0.5)
    else:
        line("(no angles)", GREY, 0.45)
    rule()

    if tgt:
        for name, v in zip(AXES, tgt):
            lo = hm.ROT_MIN if name == "thumbRot" else hm.T_MIN
            f = 0.0 if hm.T_MAX <= lo else (v - lo) / float(hm.T_MAX - lo)
            f = max(0.0, min(1.0, f))
            bx, bw = x + 96, PANEL - 140
            cv2.rectangle(canvas, (bx, y - 11), (bx + bw, y - 1), DIM, -1)
            cv2.rectangle(canvas, (bx, y - 11), (bx + int(bw * f), y - 1),
                          OK if 0.02 < f < 0.98 else BAD, -1)
            cv2.putText(canvas, name, (x, y - 2), FONT, 0.42, WHITE, 1,
                        cv2.LINE_AA)
            cv2.putText(canvas, "%4d" % v, (bx + bw + 6, y - 2), FONT, 0.4,
                        GREY, 1, cv2.LINE_AA)
            y += 20
    line("q / ESC to abort", GREY, 0.4)

    cv2.imshow(WIN, draw_cjk(canvas, texts))
    return cv2.waitKey(1) & 0xFF


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
                  hm.pose_from_world_landmarks(wl, hm.HANDEDNESS), wl)
    return out


print("八個姿勢，每個準備 %.0f 秒、保持不動 %.0f 秒（約 %.0f 秒）。"
      % (READY, HOLD, len(STEPS) * (READY + HOLD)))
print("右手、手掌朝相機。指示直接顯示在視窗上，不必看這裡。")
print("⚠️ 邊框變綠色 = 正在錄，此時手不要動，直到它跳下一個。\n")

rows, lm_rows = [], []
aborted = False
for i, (tag, zh, block) in enumerate(STEPS):
    if aborted:
        break
    print("\n>>> %s  %s" % (tag, zh))
    got = {0: 0, 1: 0}

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
            m = measure(rgb)
            if phase == "HOLD":
                for c in MODELS:
                    if m[c] is None:
                        continue
                    _, trust, why, tf, tgt, wl = m[c]
                    got[c] += trust
                    rows.append({"step": i + 1, "block": block,
                                 "model": NAME[c], "trust": trust, "why": why,
                                 "flexion": round(tf["flexion"], 2),
                                 "abduction": round(tf["abduction"], 2),
                                 "opposition": round(tf["opposition"], 2)})
                    rec = {"step": i + 1, "block": block, "model": NAME[c],
                           "trust": trust}
                    for j, p in enumerate(wl):
                        rec["x%d" % j] = round(p.x, 6)
                        rec["y%d" % j] = round(p.y, 6)
                        rec["z%d" % j] = round(p.z, 6)
                    lm_rows.append(rec)
            s = m[1] if m[1] is not None else m[0]
            key = render(frame, s[0] if s else None, i, phase, left, span,
                         s[1] if s else None, s[2] if s else "",
                         s[3] if s else None, s[4] if s else None, got)
            if key in (ord("q"), 27):
                aborted = True
                break
        if aborted:
            break

    rej = {}
    for r in rows:
        if r["step"] == i + 1 and not r["trust"]:
            rej[r["why"]] = rej.get(r["why"], 0) + 1
    print("    可用幀數: lite %d, full %d%s"
          % (got[0], got[1], "" if not rej else "   (拒絕: %s)"
             % ", ".join("%s x%d" % kv for kv in sorted(rej.items()))))

    if i == 0 and not any(got.values()) and not aborted:
        top = max(rej, key=rej.get) if rej else "沒看到手"
        print("\n停止：第一個姿勢一幀都不可用（%s）。" % top)
        if top == "hand looks flipped":
            print("MediaPipe 判定這是%s手，但校正檔鎖定 %s。請改用另一隻手。"
                  % ("左" if hm.HANDEDNESS == "Right" else "右", hm.HANDEDNESS))
        aborted = True

cap.release()
cv2.destroyAllWindows()
if aborted:
    print("\n（中止 — 已錄到的仍會存檔）")
if not rows:
    sys.exit("沒有錄到任何資料")

with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print("\n%d 列 -> %s" % (len(rows), OUT))
if lm_rows:
    with open(OUT_LM, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(lm_rows[0]))
        w.writeheader()
        w.writerows(lm_rows)
    print("%d 列原始 21 點 -> %s" % (len(lm_rows), OUT_LM))

for block in ("A", "B"):
    steps = [i + 1 for i, s in enumerate(STEPS) if s[2] == block
             and any(r["step"] == i + 1 for r in rows)]
    if not steps:
        continue
    driven = DRIVEN[block]
    print("\n\n########  BLOCK %s -- %s  ########" % (block, BLOCK_NAME[block]))
    for c in MODELS:
        per = {s: {k: [r[k] for r in rows if r["step"] == s
                       and r["model"] == NAME[c] and r["trust"]] for k in CH}
               for s in steps}
        empty = [STEPS[s - 1][0] for s in steps if len(per[s][driven]) < 3]
        if empty:
            print("\n=== %s === %s 可用幀不足三幀，無法判定" % (NAME[c], empty))
            continue

        print("\n=== %s === driven channel: %s" % (NAME[c], driven))
        print("  %-5s %5s %8s %8s %8s   %s"
              % ("step", "n", "p25", "median", "p75", "spread"))
        for s in steps:
            v = per[s][driven]
            print("  %-5s %5d %8.1f %8.1f %8.1f   %.1f .. %.1f"
                  % (STEPS[s - 1][0], len(v), pct(v, 25), pct(v, 50),
                     pct(v, 75), min(v), max(v)))

        med = [pct(per[s][driven], 50) for s in steps]
        up = all(b > a for a, b in zip(med, med[1:]))
        down = all(b < a for a, b in zip(med, med[1:]))
        clear = True
        for j in range(len(steps) - 1):
            a, b = per[steps[j]][driven], per[steps[j + 1]][driven]
            clear &= (pct(a, 75) < pct(b, 25)) if med[j + 1] > med[j] \
                else (pct(a, 25) > pct(b, 75))
        verdict = ("SEPARATED" if (up or down) and clear else
                   "ORDERED, spreads touch" if (up or down) else "MUDDLED")
        print("  --> %s   medians %s   swing %.1f"
              % (verdict, " -> ".join("%.1f" % m for m in med),
                 max(med) - min(med)))
        for k in CH:
            if k == driven:
                continue
            m = [pct(per[s][k], 50) for s in steps]
            print("      leakage %-11s %s   swing %.1f"
                  % (k, " -> ".join("%.1f" % x for x in m), max(m) - min(m)))

print("\n\n判定怎麼讀：")
print("  SEPARATED  相機分得出這幾個姿勢 — 這個通道可用")
print("  MUDDLED    分不出來 — 再怎麼調校正窗都沒用")
print("  leakage    這個區塊沒在動的通道應該幾乎不變；擺動大＝通道互相汙染")
print("  lite 是 teleop 實際跑的，full 比較準。")
print("  lite MUDDLED 但 full SEPARATED  -> 模型是瓶頸")
print("  兩個都 MUDDLED                  -> 單顆相機量不出這個角度")
