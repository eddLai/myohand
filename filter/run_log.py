#!/usr/bin/env python3
"""run_log - one directory per teleop run, written as the run happens.

    runs/2026-08-07T18-32-11/
        frames.csv    every camera frame
        meta.json     parameters, calibration, commit - what made these numbers
        summary.txt   the part a person reads

WHAT IS AND IS NOT MEASURED HERE. A live run has one path in it: the filter
is either in circuit or bypassed, and the hand followed whichever was
running. So the before/after comparison cannot be two measurements - it is
one measurement and one RECONSTRUCTION. What makes that honest is that the
reconstruction is deterministic: `tgt_*` is the raw mapping output, and
replaying it through the old gate reproduces exactly what the old path
would have commanded, every time.

Which is also why the raw stream is what gets logged rather than the
filtered one. Filter parameters will be re-tuned - the README says what
invalidates them - and a log of raw frames can be re-scored against new
parameters years later, while a log of filtered output is a record of one
afternoon's settings and nothing else.

`sent_*` is stored anyway, and it is not redundant: replaying the raw
through the shipped filter has to reproduce it, and summary.txt checks that
it does. Nothing else would catch this file quietly drifting out of step
with the code it claims to be recording. Anything the replay needs in order
to reproduce a frame is therefore a column and not a line in meta.json:
that is what `deg` is, the deadband being a live slider.

Frames are logged at whatever rate the camera manages, never resampled. The
variation in dt is data - it is what drives one-euro's alpha and the whole
dropped-frame problem - and a fixed-rate log would erase exactly the part
worth keeping. Frames the gate suppressed are logged too, for the same
reason: they are the evidence of jitter being rejected.

About 3.8 KB of CSV per second, so a five-minute run is roughly 1 MB.
"""
import csv
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import measure_jitter as mj                                    # noqa: E402
import hand_filter as hf                                       # noqa: E402

AXES = hf.AXES
FINGERS = hf.FINGERS

#: The raw columns are named exactly as measure_jitter writes them, so
#: `measure_jitter analyse` and `plot` read a run log with no conversion.
#: The rest is what a live run knows and a recording session does not.
#: `sent_*` is the FILTER'S OUTPUT for the frame, not what the caller
#: happened to transmit - the replay check has to be able to reproduce it,
#: and a caller's last-transmitted pose stops tracking the filter the
#: moment it declines to send (AUTO off, sink busy, settle gate). `sent`
#: carries transmitted-or-not instead, so what actually went out is still
#: recoverable as the value on the frames where it is 1.
#:
#: `applying` is handd's own stuck detector. Without it, a run where the
#: slave stopped consuming process data is indistinguishable from a
#: mechanism that absorbed every command.
#:
#: `deg` is the deadband tolerance in force ON THIS FRAME, and it is a
#: column rather than a line in meta.json because the operator can move it
#: while the run is flying - the UI has a slider and the manual tells them
#: to use it. Recorded once at the top, the replay rebuilds the run with a
#: threshold the filter stopped using at frame 450, and the check reports
#: DOES NOT MATCH about a run in which nothing was wrong.
#:
#: This one is NOT a cut, unlike a calibration or a camera change. Moving
#: the slider clears no state - the filter's memory runs straight through
#: it and only a threshold changes - so the run stays whole and the replay
#: follows the column. Cutting is the instrument for state that broke, and
#: pointing it at an ordinary knob would shred a run into fragments.
RUN_FIELDS = ([f"sent_{a}" for a in AXES] + ["sent", "mode", "applying",
                                             "deg"])

FIELDS = mj.MAP_FIELDS + mj.TELE_FIELDS + mj.GAIN_FIELDS + RUN_FIELDS


def _git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:                                          # noqa: BLE001
        return None


class RunLog:
    """Opened at the start of a run, fed one frame at a time, closed at the end.

    Every write is flushed as it happens: a run that ends in a crash or a
    kill still leaves the frames it managed, because the interesting runs
    are disproportionately the ones that ended badly.
    """

    def __init__(self, path, meta):
        self.path = path
        self.meta = meta
        os.makedirs(path, exist_ok=True)
        self._fh = open(os.path.join(path, "frames.csv"), "w", newline="")
        self._w = csv.writer(self._fh)
        self._w.writerow(FIELDS)
        self._gain_row = [f"{meta['gains'][a]:.4f}" for a in AXES]
        self.n = self.seen = self.sends = 0
        self.t_first = self.t_last = None

    @classmethod
    def open(cls, root, gains, filter_params, extra=None, stamp=None):
        """A directory named for when the run started, under `root`.

        One teleop session can open several of these: the filter is rebuilt
        when a calibration changes the windows its gains came from, and a
        run log describes one continuous stretch of filter, so it is cut
        there. A calibration takes most of a minute and cannot collide, but
        a camera change is also a cut and two of those inside one second
        would otherwise land on the same directory and truncate the first
        one's frames.csv.
        """
        import time
        base = stamp or time.strftime("%Y-%m-%dT%H-%M-%S")
        path, n = os.path.join(root, base), 1
        while os.path.exists(os.path.join(path, "frames.csv")):
            n += 1
            path = os.path.join(root, f"{base}-{n}")
        meta = {
            "started": os.path.basename(path),
            "git_commit": _git_commit(),
            "host": os.uname().nodename,
            "argv": sys.argv,
            "gains": gains,
            "filter": filter_params,
        }
        meta.update(extra or {})
        return cls(path, meta)

    def frame(self, t, seen, raw=None, sent=None, was_sent=False,
              mode="on", trust=True, why="", feats=None, tele=None,
              deg=None):
        """One camera frame. `raw` is the mapping output before any filter.

        `deg` is the deadband tolerance the filter was carrying for this
        frame. Left out, the column is blank and the replay falls back to
        meta's single value - which is what every log written before this
        column existed says, and the right reading of it.
        """
        self.n += 1
        if self.t_first is None:
            self.t_first = t
        self.t_last = t
        # Six decimals, not four: `t` is an epoch time near 1.8e9, so four
        # leaves 0.1 ms of resolution on a 33 ms frame - enough to move the
        # filter state a hair, which is enough to flip a sample sitting on
        # the gate threshold, which moves the staircase a whole step. That
        # read as 7% of frames failing the replay check when nothing was
        # wrong. Six decimals is about where float64 runs out at this
        # magnitude anyway.
        row = [f"{t:.6f}", 1 if seen else 0, 1 if trust else 0, why]
        if seen and raw is not None:
            self.seen += 1
            row += [f"{v:.4f}" for v in raw]
        else:
            row += [""] * len(AXES)
        f = feats or {}
        row += [f"{f[k]:.3f}" if k in f else ""
                for k in [f"curl_{x}" for x in FINGERS]
                + ["thumb_flexion", "opposition"]]
        row += ([str(v) for v in tele["ang"]] + [str(v) for v in tele["cur"]]
                + [str(v) for v in tele.get("sta") or [""] * len(AXES)]
                if tele else [""] * (3 * len(AXES)))
        row += self._gain_row
        row += ([f"{v:.4f}" for v in sent] if sent is not None
                else [""] * len(AXES))
        # Four decimals is exact for this one: the slider steps 0.5 between
        # 0.5 and 4.0, so every value it can produce survives the round trip
        # unchanged, and the threshold the replay rebuilds is the threshold
        # that flew.
        row += [1 if was_sent else 0, mode,
                "" if not tele or tele.get("applying") is None
                else (1 if tele["applying"] else 0),
                "" if deg is None else f"{deg:.4f}"]
        self.sends += 1 if was_sent else 0
        self._w.writerow(row)
        self._fh.flush()

    def close(self):
        self._fh.close()
        with open(os.path.join(self.path, "meta.json"), "w") as fh:
            json.dump(self.meta, fh, indent=2, ensure_ascii=False)
        text = summarise(self.path)
        with open(os.path.join(self.path, "summary.txt"), "w") as fh:
            fh.write(text)
        return self.path


# ---- summary -------------------------------------------------------------

def _travel(seq, i):
    return sum(abs(seq[k][i] - seq[k - 1][i]) for k in range(1, len(seq)))


def summarise(path, skip=0.0):
    """The run in words and numbers. Standard library only, on purpose.

    This is the artefact that always gets written, so it must not be able
    to fail for a reason as trivial as a plotting library being absent -
    which on the KD240, where 1.9 GB of RAM barely fits MediaPipe, is not a
    hypothetical.
    """
    meta = {}
    mpath = os.path.join(path, "meta.json")
    if os.path.exists(mpath):
        meta = json.load(open(mpath))

    with open(os.path.join(path, "frames.csv")) as fh:
        allrows = list(csv.DictReader(fh))
    rows = [r for r in allrows if r["seen"] == "1" and r[f"tgt_{AXES[0]}"]]
    out = []
    a = out.append

    # The deadband the run opened with, and what it actually ran at. meta
    # holds one value because it is written once; the column holds the rest.
    # A log from before the column falls back to meta, which is the whole
    # truth for it - there was no way to move the slider and record it.
    deg0 = (meta.get("filter") or {}).get("deg", hf.DEADBAND_DEG)
    degs = sorted({float(r["deg"]) for r in rows if r.get("deg")})

    a(f"run           {meta.get('started', os.path.basename(path))}")
    a(f"commit        {meta.get('git_commit') or '(unknown)'}")
    a(f"host          {meta.get('host', '?')}")
    if meta.get("calibration"):
        a(f"calibration   {meta['calibration']}")
    if meta.get("filter"):
        f = meta["filter"]
        band = (f"{degs[0]:g}-{degs[-1]:g} deg (the slider moved during the"
                f" run)" if len(degs) > 1 else f"{f.get('deg')} deg")
        a(f"filter        one-euro fc={f.get('mincutoff')} beta={f.get('beta')}"
          f" dcutoff={f.get('dcutoff')}, deadband={band},"
          f" dt<={f.get('dt_max')}s")
    if meta.get("sink"):
        a(f"sink          {meta['sink']}")

    if not rows:
        a("")
        a("no frames with a hand in them - nothing to summarise")
        return "\n".join(out) + "\n"

    ts = [float(r["t"]) for r in rows]
    span = ts[-1] - ts[0]
    a("")
    a(f"frames        {len(allrows)} total, {len(rows)} with a hand "
      f"({100.0*len(rows)/max(len(allrows),1):.0f}%)")
    a(f"duration      {span:.1f} s at {len(rows)/span:.1f} FPS"
      if span > 0 else "duration      (too short to rate)")
    dts = sorted(ts[i] - ts[i - 1] for i in range(1, len(ts)))
    if dts:
        a(f"frame time    median {dts[len(dts)//2]*1000:.0f} ms, "
          f"p95 {dts[int(len(dts)*0.95)]*1000:.0f} ms, "
          f"max {dts[-1]*1000:.0f} ms")
    modes = {r["mode"] for r in rows}
    if modes == {"on"}:
        which = "ON throughout"
    elif modes == {"off"}:
        which = "OFF throughout"
    else:
        which = "toggled during the run"
    a(f"filter        {which}")

    raw = [[float(r[f"tgt_{x}"]) for x in AXES] for r in rows]

    # What each path commands, over the same frames. ON is replayed rather
    # than read from sent_* so that both columns are produced the same way;
    # the check below is what earns the right to do that.
    gains = {x: float(rows[0].get(f"gain_{x}") or 0) or mj.gains()[x]
             for x in AXES}
    filt = hf.HandFilter(gains, deg=deg0)
    on, last = [], None
    for r, v in zip(rows, raw):
        vv = list(v)
        if r["trust"] != "1":
            vv[4] = vv[5] = hf.HOLD
        # Per frame, exactly where teleop does it: the operator can move
        # the deadband slider mid-run, and a replay holding the value the
        # log opened with rebuilds a filter that stopped flying at the
        # moment they touched it.
        filt.set_deadband_deg(float(r.get("deg") or deg0))
        got = filt.update(vv, float(r["t"]))
        if got is not None:
            last = got
        on.append(list(last) if last else list(v))
    off = mj.commanded_old(raw)

    a("")
    a("-- commanded travel, ANGLEACT counts over the whole run --")
    a("   filter OFF is RECONSTRUCTED: the hand followed whichever path was")
    a("   actually running. The reconstruction is deterministic, not a guess.")
    a(f"   {'axis':<12}{'filter OFF':>12}{'filter ON':>11}{'less':>7}"
      f"{'raw sd':>9}")
    for i, x in enumerate(AXES):
        t_off, t_on = _travel(off, i), _travel(on, i)
        col = [v[i] for v in raw]
        mean = sum(col) / len(col)
        sd = (sum((v - mean) ** 2 for v in col) / len(col)) ** 0.5
        cut = f"{100*(1-t_on/t_off):.0f}%" if t_off else "-"
        a(f"   {x:<12}{t_off:>12.0f}{t_on:>11.0f}{cut:>7}{sd:>9.1f}")

    # Does replaying the raw reproduce what was actually sent? If not, this
    # file is not a record of the run it claims to describe, and every
    # number above is about something else.
    #
    # Every frame with a filter output recorded, not just the ones flown
    # with the filter in circuit. teleop calls filt.update() unconditionally
    # - the filter keeps running while it is bypassed - and logs its output
    # either way, so the data was always there and skipping it left a run
    # flown entirely on `f` with no check performed on it at all.
    worst, n_cmp, n_bad, n_byp = 0.0, 0, 0, 0
    for r, v in zip(rows, on):
        if not r[f"sent_{AXES[0]}"]:
            continue
        n_cmp += 1
        n_byp += 1 if r["mode"] != "on" else 0
        d = max(abs(float(r[f"sent_{x}"]) - v[i])
                for i, x in enumerate(AXES))
        worst = max(worst, d)
        n_bad += 1 if d > 0.5 else 0
    if n_cmp:
        a("")
        # The gate is a threshold, so a sample sitting exactly on it can
        # fall either way once it has been through a text file, and the
        # staircase then differs by one whole step - which is why a
        # tolerance on the worst frame alone called an ordinary run
        # broken. A tie shows up on a few frames. Code that has really
        # diverged from what flew shows up on most of them.
        frac = n_bad / n_cmp
        ok = "matches" if frac <= 0.01 else "DOES NOT MATCH"
        a(f"replay check  {ok} what was sent: {n_cmp - n_bad}/{n_cmp} "
          f"frames identical, worst {worst:.1f} counts")
        if n_byp:
            # Worth saying plainly: on a bypassed frame the filter's output
            # never reached the hand, so the check is comparing the record
            # against the code rather than against what flew. It still
            # catches drift and precision loss; it just claims one step less.
            a(f"              {n_byp} of those were flown with the filter")
            a("              bypassed, where this compares the record against")
            a("              the code, not against what reached the hand")
        if frac > 0.01:
            a(f"              {100*frac:.0f}% of frames differ - the numbers")
            a("              above describe a filter other than the one")
            a("              this run was flying, so do not trust them")

    # The only MEASURED curve in this file.
    #
    # Both columns above are worked out afterwards from the raw stream. This
    # one is not: ANGLEACT is where the hand reports it actually got to, so
    # for whichever path was flying it closes the loop that the
    # reconstruction cannot. It answers the question the commanded numbers
    # only imply - how much of what we asked for the mechanism actually
    # executed.
    #
    # Two things it is not. It is sampled once per camera frame while the
    # hand applies its outputs every 18-27 ms, so it is not a timing
    # reference. And it carries the RH56F1's own servo lag on top of ours,
    # so the gap between commanded and actual is the whole chain's, not this
    # filter's.
    tele = [r for r in rows if r.get(f"cur_{AXES[0]}")]
    if tele:
        idx = {id(r): k for k, r in enumerate(rows)}
        on_t = [on[idx[id(r)]] for r in tele]
        ang = [[float(r[f"ang_{x}"]) for x in AXES] for r in tele]
        # A slave that has stopped consuming process data reads exactly like
        # a mechanism that absorbed every command: no travel, no current,
        # 100% absorbed. handd knows the difference and says so in `state`,
        # so the one number that must not be reported as absorption is the
        # one where nothing was being applied at all.
        napp = sum(1 for r in tele if r.get("applying") == "0")
        dead = all(_travel(ang, i) == 0 for i in range(len(AXES))) and all(
            float(r[f"cur_{x}"]) == 0 for r in tele for x in AXES)
        a("")
        a(f"-- what the hand actually did ({len(tele)}/{len(rows)} frames) --")
        if napp or dead:
            a("   !! THE HAND WAS NOT APPLYING WHAT IT WAS SENT.")
            if napp:
                a(f"   handd reported not-applying on {napp}/{len(tele)}"
                  f" frames.")
            if dead:
                a("   ANGLEACT never moved and no axis drew any current, while")
                a("   poses were being commanded.")
            a("   So 'absorbed' below is NOT the mechanism absorbing anything -")
            a("   nothing was executed. This is a bus/daemon fault, not a")
            a("   filter result, and the commanded columns above are still")
            a("   valid while these are not.")
            a("")
        a("   commanded is inferred from the raw stream; actual is ANGLEACT,")
        a("   measured. 'absorbed' is what the mechanism did not execute.")
        a("   Idle draws 0 mA on every axis (2026-08-07), so any current here")
        a("   is what these commands cost the actuator.")
        a(f"   {'axis':<12}{'commanded':>11}{'actual':>9}{'absorbed':>10}"
          f"{'cur mean':>10}{'cur max':>9}")
        for i, x in enumerate(AXES):
            cmd, act = _travel(on_t, i), _travel(ang, i)
            cur = [abs(float(r[f"cur_{x}"])) for r in tele]
            frac = f"{100*(1-act/cmd):.0f}%" if cmd else "-"
            a(f"   {x:<12}{cmd:>11.0f}{act:>9.0f}{frac:>10}"
              f"{sum(cur)/len(cur):>10.1f}{max(cur):>9.0f}")

    a("")
    a("-- to plot it --")
    # Absolute path to the script as well as to the data: this is printed
    # where teleop was run from, which is teleop/, and measure_jitter lives
    # in filter/. A command that only works from one directory is one the
    # reader has to debug before they can use it.
    # sys.executable, not "python3": the summary is written by whichever
    # interpreter ran teleop, and that is the one with matplotlib in it.
    # The bare name resolved to the system python, which has no matplotlib,
    # so the offered command failed the first time it was pasted.
    a(f"   {sys.executable} \\")
    a(f"       {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'measure_jitter.py')} \\")
    a(f"       plot {os.path.abspath(os.path.join(path, 'frames.csv'))} \\")
    # Land the figure next to the data it came from. Without -o it defaults
    # into whatever directory the reader happened to be standing in, which
    # is how you end up with filter_ab.png in teleop/ and no idea which run
    # it belongs to.
    # SVG rather than PNG: the format is taken from the extension, and on a
    # run of this size the vector file is both sharper at any zoom AND
    # smaller than a 150 dpi raster (884 KB against 1.1 MB, measured on a
    # 70 s six-axis run). Swap the extension for .pdf (smaller still) or
    # .png (with --dpi) if something downstream needs a bitmap.
    # All six, not just the fingers. `finger_travel` is the headline number
    # because the fingers are what the operator watches, and that reasoning
    # got copied into the plot command where it does not belong: it left out
    # both thumb axes, including the one whose travel was being investigated
    # at the time.
    a(f"       --axis {' '.join(AXES)} --skip 0 \\")
    a(f"       -o {os.path.abspath(os.path.join(path, 'plot.svg'))}")
    a("")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {os.path.basename(sys.argv[0])} <run-directory>\n"
                 f"  re-prints summary.txt from frames.csv, for a run whose\n"
                 f"  summary predates a change to how it is worked out")
    print(summarise(sys.argv[1]), end="")
