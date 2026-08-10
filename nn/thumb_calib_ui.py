#!/usr/bin/env python3
"""Measure the calibration windows from six HELD poses, with the pose shown
on screen.

thumb_calib.py asks for four timed sweeps and prints its instructions to a
terminal the operator cannot read while posing. The 2026-08-06 recording
shows what that costs: in the five seconds labelled "curl hard into the
palm", opposition travelled 124 degrees and flexion only 60. The operator
was rotating the thumb, not folding it, so THUMB_CLOSED was measured off
the wrong motion. Reverse leakage tops out near 16 degrees, so 124 cannot
be an artefact -- the thumb really was turning.

Two changes follow from that. Poses are HELD, not swept, so an endpoint is
the middle of a still distribution instead of the tip of a moving one. And
the pose is stated in Chinese at full size with the border green while
recording, the fix that already turned thumb_steps.py from a failed run
into a usable one.

The window this calibrates:

  P1 P2  four fingers open / fist          -> CURL_OPEN  CURL_CLOSED
  P3 P4  thumb straight / folded           -> THUMB_OPEN THUMB_CLOSED
  P5 P6  thumb in palm plane / swept over  -> OPP_MIN    OPP_MAX

P3 and P4 must differ ONLY in the two thumb joints. Keeping the thumb in
the palm plane also keeps it out from behind the palm, which is where the
landmarks break down -- the correct motion is also the one measured best.
The run checks this and says so: if opposition moved between P3 and P4,
the recording repeated the 2026-08-06 mistake and should be redone.

Every frame goes to both models. model_complexity defaults to 0 here, the
value every entry point in this repo actually runs; thumb_calib.py still
defaults to 1, which is how the current window came to be measured on a
model teleop never loads.

Writes nothing to calibration.json without --save=NAME. Note that saving
also makes the profile ACTIVE, and this machine is shared.

    ../venv/bin/python3 thumb_calib_ui.py [device] [hold_seconds]
                        [--complexity=0|1] [--save=NAME] [--note=TEXT]
                        [--replay]

    q or ESC aborts and still reports whatever was recorded. --replay
    re-runs the report over the last recording without a camera, so a
    changed threshold or percentile costs no posing.
"""
import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "camera"))
import hand_mapping as hm  # noqa: E402

opts = {a.split("=")[0]: a.split("=", 1)[1]
        for a in sys.argv[1:] if a.startswith("--") and "=" in a}
pos = [a for a in sys.argv[1:] if not a.startswith("--")]
DEV = int(pos[0]) if pos else 0
HOLD = float(pos[1]) if len(pos) > 1 else 5.0
CPLX = int(opts.get("--complexity", 0))
SAVE = opts.get("--save")
NOTE = opts.get("--note", "")
# save_calibration binds its path default at import, so reassigning
# hm.CAL_PATH does NOT redirect it -- a test that tried landed a profile in
# the live file and made it active. Pass the path explicitly instead.
CALPATH = opts.get("--cal", hm.CAL_PATH)
REPLAY = "--replay" in sys.argv
READY = 4.0

if not REPLAY:   # a replay must not need a camera stack to be installed
    import cv2
    import numpy as np
    import mediapipe as mp
    from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "thumb_calib_ui.csv")
OUT_LM = os.path.join(HERE, "thumb_calib_ui_landmarks.csv")

# key, on-screen pose, on-screen detail, channel, which end of the window
POSES = [
    ("P1", "四指張到最開", "四指盡量張直；拇指擺哪都可以",
     "curl", "CURL_OPEN", "lo"),
    ("P2", "四指握拳", "拇指放在拳頭外面，不要包進去",
     "curl", "CURL_CLOSED", "hi"),
    ("P3", "拇指伸直", "手掌攤平；拇指往旁邊張開，跟四指同一個平面",
     "flexion", "THUMB_OPEN", "lo"),
    ("P4", "拇指捲曲", "只折拇指的兩個關節，指尖倒向食指根；不抬起也不壓下",
     "flexion", "THUMB_CLOSED", "hi"),
    ("P5", "拇指張開成 L 型", "手掌攤平；拇指往外側張開，跟四指同一平面。"
                              "不是壓在手心上！跟 P3 同一個手型",
     "opposition", "OPP_MIN", "lo"),
    ("P6", "拇指轉到手心前方", "拇指打直，往小指方向轉過手心；"
                              "只轉不折，不要真的碰到小指",
     "opposition", "OPP_MAX", "hi"),
]
# Pairs that should differ in one channel only, and the channel that
# betrays a different joint having moved. The two are not equally the
# operator's fault, so they do not carry the same consequence.
#
# P3->P4 is: did you fold the thumb, or rotate it? That is entirely up to
# the operator -- the 2026-08-06 recording drifted 124 degrees here and
# was measuring the wrong joint, while a careful run on 2026-08-07 came in
# at 0.1. Worth refusing over.
#
# P5->P6 is: did flexion stay put while you swept? It does not, and not
# because of anything the operator did. Sweeping the thumb across the palm
# moves the measured flexion by about 42 degrees on real frames; it is a
# property of the landmarks under occlusion, measured and written up in
# README.md. Refusing over it would reject every honest recording, which
# teaches people to route around the check. It is reported and left alone.
# POSES is filtered under --replay to whatever the recording holds, so the
# completeness check below has to remember the full set from up here.
ALL_KEYS = tuple(p[4] for p in POSES)

CHECKS = [("P3", "P4", "opposition", "折拇指的時候不該轉", "refuse"),
          ("P5", "P6", "flexion", "掃對掌會帶到彎曲讀數（模型的已知洩漏，非操作問題）",
           "warn")]
CONTAMINATED = 30.0
# an endpoint that jumps this far from the window in force usually means a
# pose was demonstrated less fully than last time, not that the hand changed
MOVED_A_LOT = 15.0

CH = ("flexion", "abduction", "opposition", "curl")
MODELS = (0, 1)
NAME = {0: "lite", 1: "full"}
AXES = ("pinky", "ring", "middle", "index", "thumbBend", "thumbRot")

WIN = "thumb calibration"
PANEL = 330
CAP_W, CAP_H = 1280, 720
FONT = None if REPLAY else cv2.FONT_HERSHEY_SIMPLEX
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
        d.text((x, y), text, font=cjk_font(size), fill=(col[2], col[1], col[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q / 100.0 * (len(s) - 1)))))]


def features(wl):
    """The four numbers the windows are cut from. curl pools all four
    fingers because they share one window, so the window has to cover the
    spread between them, not just one finger's."""
    f = hm.thumb_features(wl, hm.HANDEDNESS)
    curls = [hm.finger_curl(wl, c) for c in hm.FINGER_CHAINS.values()]
    return {"flexion": f["flexion"], "abduction": f["abduction"],
            "opposition": f["opposition"], "curl": curls}


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
        out[c] = (res, int(trust), why, features(wl),
                  hm.pose_from_world_landmarks(wl, hm.HANDEDNESS), wl,
                  lbl.label, lbl.score)
    return out


def render(frame, res, idx, phase, left, span, trust, why, fe, tgt, got):
    tag, zh, detail, chan, key_name, _end = POSES[idx]
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

    band = canvas[:118, :w].copy()
    canvas[:118, :w] = cv2.addWeighted(band, 0.25, np.zeros_like(band), 0, 0)
    cv2.putText(canvas, tag, (16, 66), FONT, 1.9, accent, 4, cv2.LINE_AA)
    cv2.putText(canvas, "%d/%d" % (idx + 1, len(POSES)), (16, 90), FONT,
                0.5, GREY, 1, cv2.LINE_AA)

    done = 0.0 if span <= 0 else max(0.0, min(1.0, 1.0 - left / span))
    cv2.rectangle(canvas, (0, h - 12), (w, h), (40, 40, 40), -1)
    cv2.rectangle(canvas, (0, h - 12), (int(w * done), h), accent, -1)
    cv2.rectangle(canvas, (0, 0), (w - 1, h - 1), accent, 6)

    texts = [(zh, (112, 8), 46, WHITE), (detail, (112, 66), 26, GREY)]
    if holding:
        texts.append(("● 錄影中，保持不動  %.1f 秒" % left, (16, h - 62), 32, OK))
    else:
        texts.append(("準備… %d" % int(math.ceil(left)), (16, h - 62), 32, HI))

    x, y = w + 14, 32

    def line(text, col=WHITE, scale=0.5, dy=22, thick=1):
        nonlocal y
        cv2.putText(canvas, text, (x, y), FONT, scale, col, thick, cv2.LINE_AA)
        y += dy

    def rule():
        nonlocal y
        cv2.line(canvas, (x, y - 8), (w + PANEL - 14, y - 8), DIM, 1)
        y += 6

    line("measuring %s" % key_name, HI, 0.5, 26)
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

    if fe:
        for k in ("flexion", "abduction", "opposition"):
            col = HI if k == chan else WHITE
            line("%-11s %7.1f" % (k, fe[k]), col, 0.5)
        col = HI if chan == "curl" else WHITE
        line("%-11s %7.1f" % ("curl(med)", pct(fe["curl"], 50)), col, 0.5)
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


rows, lm_rows = [], []
aborted = False

if REPLAY:
    if not os.path.exists(OUT):
        sys.exit("no recording to replay: %s" % OUT)
    rows = list(csv.DictReader(open(OUT)))
    for r in rows:
        r["trust"] = int(r["trust"])
    print("replaying %d rows from %s (camera not opened)\n" % (len(rows), OUT))
    POSES = [p for p in POSES if any(r["pose"] == p[0] for r in rows)]

if not REPLAY:
    print("六個姿勢，每個準備 %.0f 秒、保持不動 %.0f 秒（約 %.0f 秒）。"
          % (READY, HOLD, len(POSES) * (READY + HOLD)))
    print("%s手、手掌朝相機。指示顯示在視窗上，不必看這裡。" % (
        "右" if hm.HANDEDNESS == "Right" else "左"))
    print("⚠️ 邊框變綠 = 正在錄，手不要動，直到它跳下一個。")
    print("提案的窗會從 model_complexity=%d (%s) 算，另一個只列出來對照。\n"
          % (CPLX, NAME[CPLX]))

    draw = mp.solutions.drawing_utils
    styles = mp.solutions.drawing_styles
    nets = {c: mp.solutions.hands.Hands(max_num_hands=1, model_complexity=c,
                                        min_detection_confidence=0.6,
                                        min_tracking_confidence=0.5)
            for c in MODELS}
    cap = cv2.VideoCapture(DEV)
    if not cap.isOpened():
        sys.exit("cannot open camera %d" % DEV)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_H)
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) + PANEL,
                     int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

for i, (tag, zh, detail, chan, key_name, end) in enumerate([] if REPLAY else POSES):
    if aborted:
        break
    print("\n>>> %s  %s — %s" % (tag, zh, detail))
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
            m = measure(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if phase == "HOLD":
                for c in MODELS:
                    if m[c] is None:
                        continue
                    _, trust, why, fe, _tgt, wl, lbl_s, score = m[c]
                    got[c] += trust
                    rec = {"pose": tag, "model": NAME[c], "trust": trust,
                           "why": why, "label": lbl_s,
                           "score": round(score, 3)}
                    for k in ("flexion", "abduction", "opposition"):
                        rec[k] = round(fe[k], 2)
                    for n, cu in zip(AXES, fe["curl"]):
                        rec["curl_" + n] = round(cu, 2)
                    rows.append(rec)
                    lr = {"pose": tag, "model": NAME[c], "trust": trust}
                    for j, p in enumerate(wl):
                        lr["x%d" % j] = round(p.x, 6)
                        lr["y%d" % j] = round(p.y, 6)
                        lr["z%d" % j] = round(p.z, 6)
                    lm_rows.append(lr)
            s = m[CPLX] if m[CPLX] is not None else m[1 - CPLX]
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
        if r["pose"] == tag and not r["trust"]:
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

if not REPLAY:
    cap.release()
    cv2.destroyAllWindows()
    # destroyAllWindows only queues the close; without the GUI loop running
    # once more the window sits there as a dead rectangle over whatever
    # launched us, for as long as the report below takes to print
    # destroyAllWindows only queues the close. One waitKey is not always
    # enough for the window manager to retire the frame, and a dead
    # rectangle sitting over whatever launched us reads as a hang at the
    # exact moment the operator is waiting to see whether it worked.
    for _ in range(10):
        cv2.waitKey(30)
    if aborted:
        print("\n（中止 — 已錄到的仍會分析）")
    if not rows:
        sys.exit("沒有錄到任何資料")

    for path, data in ((OUT, rows), (OUT_LM, lm_rows)):
        if not data:
            continue
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
        print("\n%d 列 -> %s" % (len(data), path))


def values(pose, model, chan):
    """Trusted readings of one channel in one pose. curl is every finger's,
    pooled, since one window serves all four."""
    out = []
    for r in rows:
        if r["pose"] != pose or r["model"] != NAME[model] or not r["trust"]:
            continue
        if chan == "curl":
            out += [float(r["curl_" + n]) for n in AXES[:4]]
        else:
            out.append(float(r[chan]))
    return out


print("\n\n########  每個姿勢量到什麼  ########")
for model in MODELS:
    print("\n=== %s%s ===" % (NAME[model], "   <- 提案用這個" if model == CPLX else ""))
    print("  %-4s %-14s %-12s %6s %8s %8s %8s"
          % ("pose", "window key", "channel", "n", "p10", "median", "p90"))
    for tag, zh, _d, chan, key_name, end in POSES:
        v = values(tag, model, chan)
        if len(v) < 3:
            print("  %-4s %-14s %-12s %6s   可用幀不足" % (tag, key_name, chan, len(v)))
            continue
        print("  %-4s %-14s %-12s %6d %8.1f %8.1f %8.1f"
              % (tag, key_name, chan, len(v), pct(v, 10), pct(v, 50), pct(v, 90)))

print("\n\n########  動作檢查：量 A 的時候有沒有動到 B  ########")
print("（2026-08-06 那次就是敗在這裡：標示為「捲曲」的五秒裡，")
print("  opposition 走了 124 度、flexion 只走 60，量到的是轉不是折）\n")
contaminated = []
for a, b, chan, msg, mode in CHECKS:
    va, vb = values(a, CPLX, chan), values(b, CPLX, chan)
    if len(va) < 3 or len(vb) < 3:
        print("  %s -> %s  %-12s 可用幀不足，無法檢查" % (a, b, chan))
        continue
    drift = abs(pct(vb, 50) - pct(va, 50))
    over = drift > CONTAMINATED
    if over and mode == "refuse":
        contaminated.append((a, b, chan, drift, msg))
        verdict = "⛔ 汙染，拒絕寫入 — %s" % msg
    elif over:
        verdict = "⚠️ %s" % msg
    else:
        verdict = "OK"
    print("  %s -> %s  %-12s 變化 %6.1f°   %s" % (a, b, chan, drift, verdict))

print("\n\n########  提案的窗（%s）  ########" % NAME[CPLX])
print("端點取姿勢分佈的 p10 / p90，兩端各留約一成餘裕。")
print("%-14s %10s %12s   %s" % ("key", "current", "proposed", "change"))
proposed = {}
for tag, zh, _d, chan, key_name, end in POSES:
    v = values(tag, CPLX, chan)
    if len(v) < 3:
        print("%-14s %10s %12s   可用幀不足 → 這個鍵會缺，不會沿用現值"
              % (key_name, getattr(hm, key_name), "-"))
        continue
    val = round(pct(v, 10 if end == "lo" else 90), 1)
    proposed[key_name] = val
    cur = getattr(hm, key_name)
    moved = val - cur
    print("%-14s %10s %12s   %+.1f%s"
          % (key_name, cur, val, moved,
             "   ← 跟現行差很多，%s 這次做得夠滿嗎" % tag
             if abs(moved) > MOVED_A_LOT else ""))

for lo_k, hi_k in (("CURL_OPEN", "CURL_CLOSED"), ("THUMB_OPEN", "THUMB_CLOSED"),
                   ("OPP_MIN", "OPP_MAX")):
    if lo_k in proposed and hi_k in proposed and proposed[lo_k] >= proposed[hi_k]:
        print("⚠️ %s (%.1f) 沒有小於 %s (%.1f) — 這兩個姿勢做反了，或量錯了"
              % (lo_k, proposed[lo_k], hi_k, proposed[hi_k]))

if not SAVE:
    print("\n沒有寫入任何東西。確認上面沒有 ⚠️ 之後，加 --save=名稱 重跑，")
    print("或直接把這段輸出貼回來。")
elif aborted:
    print("\n拒絕寫入：這次是中止的，六個姿勢沒有走完。")
    print("錄到的資料仍然存進 CSV 了，但不會寫成 profile。")
elif set(ALL_KEYS) - set(proposed):
    miss = [k for k in ALL_KEYS if k not in proposed]
    print("\n拒絕寫入：%d 個窗沒有量到 — %s" % (len(miss), ", ".join(miss)))
    print("缺的鍵不會沿用現行 profile，下次啟動會退回程式碼字面值：")
    for k in miss:
        print("    %-14s → %s" % (k, getattr(hm, k)))
    print("重錄那幾個姿勢比存一個半套的窗便宜。")
elif contaminated:
    print("\n拒絕寫入：動作檢查有 ⚠️，這批資料量的不是它宣稱的那個關節。")
    print("重錄一次比存一個錯的窗便宜。真要存就先把 CHECKS 的問題解決。")
else:
    proposed["HANDEDNESS"] = hm.HANDEDNESS
    name = hm.save_calibration(proposed, name=SAVE, path=CALPATH,
                               note=NOTE or "held poses, thumb_calib_ui.py, "
                                            "model_complexity=%d" % CPLX)
    print("\n寫入 profile '%s' -> %s" % (name, CALPATH))
    print("⚠️ 它同時被設為 active，而這台機器是共用的。")
