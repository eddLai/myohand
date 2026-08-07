#!/usr/bin/env python3
"""How far palm-ward will the thumb rotation actually go?

hand_mapping.py:47 leaves this open on purpose. ROT_MIN is 1226, carried
arithmetically from 700 on the old 0..2000 scale, while a 2026-08-05
on-hand test measured the palm-ward stop directly at ANGLEACT 600. The
two have never been reconciled, and the comment says plainly that whoever
can stand next to the hand should decide. Teleop therefore commands 65%
of the rotation range and leaves the last third unused, which is what an
operator sees as the thumb opposing only as far as the index.

This answers it without editing anything. The daemon has no ROT_MIN of
its own -- that constant lives on the camera side -- so the axis can be
stepped down through hand_client while the driver's own guard stays in
force. Nothing here bypasses the safety layer; it reports what the layer
did.

Method, per step: open the fingers so the index interlock cannot fire,
command one thumb_rot, wait for the angle to settle, then record where it
got to, what current it drew, and whether the guard rewrote the target.
Stops early on a stall, on the guard intervening, or on the angle
refusing to follow -- and parks the thumb open again on the way out,
including after Ctrl+C.

Needs handd. In another terminal:

    cd ~/myohand/hand_fw && ./handd --iface=eno1 --socket=/tmp/inspire_hand.sock

Then, with a clear view of the hand and a finger on the power:

    ../venv/bin/python3 rot_floor_probe.py --go

    --go        actually move. Without it this prints the plan and exits.
    --floor=N   stop stepping at N instead of 890 (the mechanism's own stop)
    --force=N   writing force, default 400 - deliberately below the
                driver's STALL_CUR so a jam shows up as a stall, not as a
                shove
"""
import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# a hardware run is expensive and unrepeatable-on-demand; printing to a
# terminal that later scrolls away lost one outright
OUT = os.path.join(HERE, "rot_floor_probe.csv")
sys.path.insert(0, os.path.join(HERE, os.pardir, "hand_fw"))
import hand_client  # noqa: E402
import hand_scale  # noqa: E402

opts = {a.split("=")[0]: a.split("=", 1)[1]
        for a in sys.argv[1:] if a.startswith("--") and "=" in a}
GO = "--go" in sys.argv
FLOOR = int(opts.get("--floor", hand_scale.TARGET_MIN))
FORCE = int(opts.get("--force", 400))
SPEED = int(opts.get("--speed", 300))

AX = ("pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot")
ROT = 5
OPEN = hand_scale.TARGET_MAX
ROT_MIN_TELEOP = 1226          # hand_mapping.py:56, the value under test
STALL_CUR = 400                # hand_safety.c:37
TOL = 40                       # ANGLEACT counts we call "arrived"
SETTLE = 3.0                   # seconds to give one step

STEPS = [s for s in (1226, 1150, 1080, 1010, 940, 890) if s >= FLOOR]
# a pose with everything else out of the way: the index interlock needs an
# open index, and a straight thumb keeps the bend axis out of the question
BASE = [OPEN, OPEN, OPEN, OPEN, OPEN, OPEN]

print(__doc__.split("Needs handd")[0].rstrip())
print("\n計畫：四指張開，拇指打直，然後 thumb_rot 依序送")
print("  " + " -> ".join(str(s) for s in STEPS))
print("force=%d speed=%d   停止條件：電流 > %d、守衛介入、或角度跟不上"
      % (FORCE, SPEED, STALL_CUR))
print("teleop 目前的下限是 %d；機構自己的下限是 %d"
      % (ROT_MIN_TELEOP, hand_scale.TARGET_MIN))
if not GO:
    print("\n沒有 --go，不會動任何東西。確認手看得到、電源摸得到再加 --go 重跑。")
    sys.exit(0)


def settled(hand, want, limit=SETTLE):
    """Wait for the axis to stop arguing, and report the last reading.

    Parking needs a longer limit than a step does: a step moves 70 counts,
    the way home moves the whole sweep, and the first version called that
    a failure to park when it was only slower.
    """
    last = None
    t0 = time.time()
    while time.time() - t0 < limit:
        st = hand.state()
        ang, cur, sta = st["ang"][ROT], st["cur"][ROT], st["sta"][ROT]
        last = (ang, cur, sta)
        if abs(ang - want) <= TOL:
            time.sleep(0.2)                     # let it hold, then re-read
            st = hand.state()
            return (st["ang"][ROT], st["cur"][ROT], st["sta"][ROT]), True
        time.sleep(0.1)
    return last, False


rows = []
hand = hand_client.HandClient()
try:
    hand.connect()
    if hand.simulated:
        print("\n⚠️ daemon 是 --simulate 起的，沒有真的手。數字不算數。")
    hand.profile(FORCE, SPEED)

    print("\n先張開，讓食指離開拇指的旋轉路徑…")
    hand.target(BASE)
    time.sleep(1.5)
    st = hand.state()
    print("起點 ang = %s" % st["ang"])

    for want in STEPS:
        tgt = list(BASE)
        tgt[ROT] = want
        reply = hand.target(tgt)
        (ang, cur, sta), arrived = settled(hand, want)
        note = reply.get("guard_note", "") or ""
        rows.append((want, ang, cur, sta, arrived, note))

        flags = []
        if reply.get("guarded"):
            flags.append("守衛改寫: %s" % note)
        if cur > STALL_CUR:
            flags.append("電流 %d > STALL_CUR %d" % (cur, STALL_CUR))
        if not arrived:
            flags.append("角度只到 %d，差 %d" % (ang, want - ang))
        print("  送 %4d -> ang %4d  cur %4d  sta %d   %s"
              % (want, ang, cur, sta, "OK" if not flags else " | ".join(flags)))
        if flags:
            print("\n停在這裡——已經摸到極限了，不再往下壓。")
            break
finally:
    try:
        print("\n收尾：把拇指轉回張開位置…")
        hand.target(BASE)
        # wait for it rather than sleeping a guess: a fixed 1.2s left the
        # axis mid-sweep, so the run ended reporting a position the thumb
        # was still travelling through
        (ang, _cur, _sta), parked = settled(hand, OPEN, limit=12.0)
        print("末態 ang = %s%s" % (hand.state()["ang"],
                                   "" if parked else "  ⚠️ 拇指還沒回到定位"))
    except Exception as e:                       # never leave without trying
        print("收尾失敗：%s" % e)
    hand.close()

if not rows:
    sys.exit("沒有量到任何一步")

with open(OUT, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(("sent", "reached", "current", "sta", "arrived", "guard_note"))
    w.writerows(rows)
print("\n%d 步 -> %s" % (len(rows), OUT))

print("\n\n########  結果  ########")
print("  %6s %6s %6s %5s   %s" % ("送出", "到達", "電流", "sta", "判定"))
for want, ang, cur, sta, arrived, note in rows:
    print("  %6d %6d %6d %5d   %s"
          % (want, ang, cur, sta,
             "到位" if arrived else "沒到位" + (" (%s)" % note if note else "")))

deepest = min((r[1] for r in rows if r[4]), default=None)
if deepest is None:
    print("\n連 %d 都沒到位——目前的 ROT_MIN 已經是能到的位置，不用改。" % STEPS[0])
else:
    gain = ROT_MIN_TELEOP - deepest
    print("\n實際到得了的最低點：ANGLEACT %d" % deepest)
    if gain > TOL:
        span = float(hand_scale.TARGET_MAX - hand_scale.TARGET_MIN)
        print("比 teleop 現在的下限 %d 低 %d counts（滿刻度的 %.0f%%）。"
              % (ROT_MIN_TELEOP, gain, 100.0 * gain / span))
        print("把 hand_mapping.py 的 ROT_MIN 改成 %d 就能拿回這段行程——"
              "那是共用檔，要開分支。" % deepest)
    else:
        print("跟現在的 %d 沒有實質差別，ROT_MIN 維持原樣。" % ROT_MIN_TELEOP)
