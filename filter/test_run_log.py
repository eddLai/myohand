#!/usr/bin/env python3
"""Offline checks for the run log. No camera, no hand, no daemon.

    python3 test_run_log.py

The log is the only record a run leaves, so what is asserted here is the
part that would make it a record of the wrong thing: raw rather than
filtered frames, a reconstruction that does not reproduce what was sent,
and a summary that stops being written the moment anything is missing.
"""
import csv
import json
import os
import random
import sys
import tempfile

import hand_filter as hf
import run_log

AXES = hf.AXES
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def fly(path, n=300, hold_thumb_from=None, mode="on", telemetry=True,
        seed=4):
    """Fly a synthetic run through the same objects teleop uses."""
    r = random.Random(seed)
    log = run_log.RunLog(path, {
        "started": "test", "git_commit": "0000000", "host": "test",
        "argv": ["test"], "gains": {a: 6.04 for a in AXES},
        "filter": {"mincutoff": hf.MINCUTOFF, "beta": hf.BETA,
                   "dcutoff": hf.DCUTOFF, "deg": 1.5, "dt_max": hf.DT_MAX},
        "sink": "none", "calibration": "(module defaults)"})
    filt = hf.HandFilter({a: 6.04 for a in AXES}, deg=1.5)
    last = None
    for k in range(n):
        t = k / 30.0
        trust = hold_thumb_from is None or k < hold_thumb_from
        raw = [1500 + r.gauss(0, 11) for _ in AXES]
        fed = list(raw)
        if not trust:
            fed[4] = fed[5] = hf.HOLD
        out = filt.update(fed, t)
        if out is not None:
            last = out
        sent = list(last) if last else None
        log.frame(t=t, seen=True, raw=raw, sent=sent,
                  was_sent=bool(filt.changed), mode=mode, trust=trust,
                  tele={"ang": [1500] * 6, "cur": [3] * 6} if telemetry
                  else None)
    return log.close()


with tempfile.TemporaryDirectory() as d:
    run = fly(os.path.join(d, "plain"))

    # ---- the three artefacts exist ------------------------------------
    for f in ("frames.csv", "meta.json", "summary.txt"):
        check(f"{f} is written", os.path.exists(os.path.join(run, f)))

    rows = list(csv.DictReader(open(os.path.join(run, "frames.csv"))))
    check("one row per frame", len(rows) == 300)

    # ---- it logs RAW, not filtered ------------------------------------
    #
    # The point of the whole format. A log of filtered output records one
    # afternoon's parameters; a log of raw frames can be re-scored against
    # whatever the parameters become.
    raw_sd = (lambda c: (sum((v - sum(c) / len(c)) ** 2 for v in c) / len(c)) ** .5)(
        [float(r["tgt_pinky"]) for r in rows])
    sent_sd = (lambda c: (sum((v - sum(c) / len(c)) ** 2 for v in c) / len(c)) ** .5)(
        [float(r["sent_pinky"]) for r in rows])
    check("tgt_* is the raw signal, not the filtered one",
          raw_sd > 4 * sent_sd,
          f"raw sd {raw_sd:.1f} against sent sd {sent_sd:.1f}")

    # ---- the reconstruction has to reproduce what was sent ------------
    text = open(os.path.join(run, "summary.txt")).read()
    check("the summary checks its replay against what was sent",
          "replay check" in text and "matches" in text
          and "DOES NOT MATCH" not in text,
          [ln.strip() for ln in text.splitlines() if "replay" in ln][0]
          if "replay check" in text else "no replay line")

    # A still signal almost never lands on the gate threshold, so it cannot
    # exercise the replay at all - the first moving run showed 9.0 counts of
    # "drift" that was only a rounding tie flipping a whole staircase step.
    # This is that case, and it has to come out clean.
    moving = os.path.join(d, "moving")
    log = run_log.RunLog(moving, {
        "started": "m", "gains": {a: 6.04 for a in AXES},
        "filter": {"mincutoff": hf.MINCUTOFF, "beta": hf.BETA,
                   "dcutoff": hf.DCUTOFF, "deg": 1.5, "dt_max": hf.DT_MAX}})
    rr = random.Random(9)
    fm = hf.HandFilter({a: 6.04 for a in AXES}, deg=1.5)
    lastm = None
    # An epoch-scale clock, because that is what teleop passes and it is
    # where the precision goes: 1.8e9 leaves far fewer decimals for the
    # fractional second than a stopwatch starting at zero does.
    T0 = 1786097111.7934
    for k in range(900):
        raw = [1500 + 300 * (k // 90 % 2) + rr.gauss(0, 11) for _ in AXES]
        got = fm.update(raw, T0 + k / 30.0)
        if got is not None:
            lastm = got
        log.frame(t=T0 + k / 30.0, seen=True, raw=raw, sent=lastm,
                  was_sent=bool(fm.changed), mode="on", trust=True)
    mtext = open(os.path.join(log.close(), "summary.txt")).read()
    mline = [ln for ln in mtext.splitlines() if "replay check" in ln][0]
    check("a moving run replays exactly, ties and all",
          "DOES NOT MATCH" not in mline, mline.strip())

    # A log whose sent_* disagrees with a replay is describing a different
    # filter, and saying so is the only thing that can catch that.
    bad = os.path.join(d, "tampered")
    fly(bad)
    p = os.path.join(bad, "frames.csv")
    rs = list(csv.DictReader(open(p)))
    for r in rs[120:]:
        r["sent_pinky"] = f"{float(r['sent_pinky']) + 40:.1f}"
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=run_log.FIELDS)
        w.writeheader()
        w.writerows(rs)
    check("and says so loudly when they disagree",
          "DOES NOT MATCH" in run_log.summarise(bad))

    # ---- both paths get reported --------------------------------------
    check("the summary reports filter OFF and filter ON side by side",
          "filter OFF" in text and "filter ON" in text)
    check("and labels the OFF column as reconstructed rather than measured",
          "RECONSTRUCTED" in text)
    def table(txt, heading):
        """The axis rows of one named section, not of every section.

        There is more than one axis-keyed table in the summary now, and a
        parser that just matches leading axis names silently reads columns
        out of whichever one it hits first.
        """
        lines, on, rows_ = txt.splitlines(), False, []
        for ln in lines:
            if heading in ln:
                on = True
                continue
            if on and ln.startswith("-- "):
                break
            if on and ln.strip().startswith(tuple(AXES)):
                rows_.append(ln.split())
        return rows_

    cmd = table(text, "commanded travel")
    check("the commanded table covers all six axes", len(cmd) == 6,
          f"{len(cmd)} rows")
    check("filter ON commands less travel than the reconstructed OFF",
          all(float(c[1]) > float(c[2]) for c in cmd))

    # ---- the measured curve --------------------------------------------
    #
    # Everything else in the summary is worked out from the raw stream.
    # ANGLEACT is the one column that was actually observed, so it is the
    # only thing that can say how much of what was commanded the mechanism
    # really executed.
    check("what the hand actually did is reported when telemetry was recorded",
          "what the hand actually did" in text)
    act = table(text, "what the hand actually did")
    check("and it sets measured actual against inferred commanded",
          len(act) == 6 and all(len(r) >= 4 for r in act)
          and "commanded" in text and "absorbed" in text)
    check("the two tables are not the same numbers under another heading",
          [r[1] for r in act] != [r[1] for r in cmd],
          "commanded-vs-actual would be vacuous if they were")
    # The 2026-08-07 run that provoked this: handd's slave had stopped
    # consuming process data, so ANGLEACT never moved and no axis drew
    # current - which reads identically to a mechanism that absorbed every
    # command, and the summary duly reported 100% absorbed on all six axes
    # as though it were a fact about the hand.
    for name, applying, cur in (("stuck", False, 0), ("silent", None, 0)):
        pth = os.path.join(d, name)
        log = run_log.RunLog(pth, {
            "started": name, "gains": {a: 6.04 for a in AXES},
            "filter": {"mincutoff": hf.MINCUTOFF, "beta": hf.BETA,
                       "dcutoff": hf.DCUTOFF, "deg": 1.5,
                       "dt_max": hf.DT_MAX}})
        fd = hf.HandFilter({a: 6.04 for a in AXES}, deg=1.5)
        rd, lastd = random.Random(3), None
        for k in range(300):
            raw = [1500 + 200 * (k // 60 % 2) + rd.gauss(0, 11) for _ in AXES]
            got = fd.update(raw, 1786097111.0 + k / 30.0)
            if got is not None:
                lastd = got
            t = {"ang": [1500] * 6, "cur": [cur] * 6}
            if applying is not None:
                t["applying"] = applying
            log.frame(t=1786097111.0 + k / 30.0, seen=True, raw=raw,
                      sent=lastd, was_sent=bool(fd.changed), mode="on",
                      trust=True, tele=t)
        dt = open(os.path.join(log.close(), "summary.txt")).read()
        check(f"a hand that executed nothing is called out, not scored ({name})",
              "NOT APPLYING WHAT IT WAS SENT" in dt
              and "NOT the mechanism absorbing" in dt)
        check(f"and the commanded columns are still offered as valid ({name})",
              "commanded columns above are still" in dt)

    no_tele = fly(os.path.join(d, "notele"), telemetry=False)
    check("and the section is absent when nothing was measured",
          "what the hand actually did"
          not in open(os.path.join(no_tele, "summary.txt")).read())

    # ---- the plot command is offered, not run --------------------------
    #
    # matplotlib must never be needed to finish a run: the KD240 has 1.9 GB
    # of RAM and barely fits MediaPipe.
    # Absolute on both halves. The first version printed a bare script name
    # into a summary that is displayed wherever teleop was run from - which
    # is teleop/, while measure_jitter lives in filter/ - so the command it
    # offered failed with "No such file or directory" the first time it was
    # copied out of a real run.
    plot_cmd = text.split("-- to plot it --")[1]
    check("the summary hands over a plot command that runs from anywhere",
          "/measure_jitter.py" in plot_cmd
          and os.path.isabs(
              [w for w in plot_cmd.split() if w.endswith("frames.csv")][0])
          and "plot " in plot_cmd,
          "both the script and the data are absolute")
    # It offered only the four fingers for a while, which quietly hid both
    # thumb axes - including thumb_bend, while thumb_bend was the thing
    # under investigation.
    check("and plots every axis, not just the ones in finger_travel",
          all(a in plot_cmd for a in AXES),
          ", ".join(a for a in AXES if a not in plot_cmd) or "all six present")
    check("and writes the figure into the run directory",
          any(w.endswith(".svg") and os.path.isabs(w)
              for w in plot_cmd.split()))
    # Actually deny it the library rather than grepping the source for the
    # word - the first version of this check failed the moment a comment
    # explained why matplotlib is absent.
    blocked = os.path.join(d, "blocked")
    fly(blocked)
    saved = sys.modules.get("matplotlib")
    sys.modules["matplotlib"] = None          # any import of it now raises
    try:
        run_log.summarise(blocked)
        ok = True
    except Exception as e:                    # noqa: BLE001
        ok, why = False, f"{type(e).__name__}: {e}"
    finally:
        if saved is None:
            sys.modules.pop("matplotlib", None)
        else:
            sys.modules["matplotlib"] = saved
    check("summarise still works with matplotlib made unimportable", ok)

    # ---- holds ---------------------------------------------------------
    held = fly(os.path.join(d, "held"), hold_thumb_from=150)
    hrows = list(csv.DictReader(open(os.path.join(held, "frames.csv"))))
    check("a held frame still logs the raw value it was given",
          all(r["tgt_thumb_rot"] not in ("", "-1.0") for r in hrows),
          "HOLD is recorded in the trust column, not by destroying the sample")
    check("and the trust column marks it",
          sum(1 for r in hrows if r["trust"] == "0") == 150)
    check("a run with holds still replays cleanly",
          "DOES NOT MATCH" not in open(os.path.join(held, "summary.txt")).read())

    # ---- meta ----------------------------------------------------------
    meta = json.load(open(os.path.join(run, "meta.json")))
    check("meta records what produced the numbers",
          meta["filter"]["mincutoff"] == hf.MINCUTOFF
          and "gains" in meta and "git_commit" in meta)

    # ---- an empty run must not crash the close path --------------------
    empty = os.path.join(d, "empty")
    log = run_log.RunLog(empty, {"started": "x", "gains": {a: 6.0 for a in AXES}})
    log.close()
    check("a run with no frames still closes and says so",
          "nothing to summarise"
          in open(os.path.join(empty, "summary.txt")).read())


print()
if fails:
    print(f"{len(fails)} check(s) failed: {', '.join(fails)}")
    sys.exit(1)
print("all checks passed")
