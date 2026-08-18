#!/usr/bin/env python3
"""plot_response - what the shipped one-euro does to a sine, drawn.

Writes `response_gain.svg` and `response_phase.svg` next to this file.
Standard library only, on purpose: `analyse` already runs without
matplotlib and this is the same kind of question - it should stay
answerable on a machine that has nothing installed but python3.

WHAT A FREQUENCY RESPONSE MEANS FOR THIS FILTER, AND WHAT IT DOES NOT.
one-euro is not LTI. Its cutoff is a function of the estimated speed, so
there is no single transfer function to plot - only a family, one curve
per frozen cutoff. Every curve here is "the filter, if the operator's
hand held that speed". The operating points are not round numbers picked
to look reasonable: they are the speeds the shipped filter actually
estimated on still.csv and moving.csv, run through the real `_OneEuro`
below, so a curve on this plot is a speed that happened.

The hysteresis stage has no frequency response at all - a deadband is a
hard nonlinearity, and a sine below the threshold comes out as silence
rather than as an attenuated sine. These plots describe stage 1 of 3.

    python3 plot_response.py            # -> response_gain.svg, response_phase.svg
"""
import cmath
import csv
import math
import os
import statistics

import hand_filter as hf
from hand_filter import AXES, BETA, MINCUTOFF

HERE = os.path.dirname(os.path.abspath(__file__))

#: The recordings the operating points are read from. Same three files
#: MINCUTOFF and DT_MAX were fitted on; see the filter/README.
STILL = os.path.join(HERE, "still.csv")
MOVING = os.path.join(HERE, "moving.csv")

#: The old teleop_app path: a fixed per-frame weight, so its time constant
#: is whatever the frame rate makes it - 77 ms at 30 FPS. Drawn as the
#: baseline this filter replaces, at the frame rate where it looks best.
LEGACY_TAU = 0.077    # seconds


# ---- the response --------------------------------------------------------
#
# Exact for the discrete filter as written, not the continuous-time
# approximation of it. `_OneEuro._alpha` builds a = 1/(1 + tau/dt), and
# one step of `x_hat += a*(x - x_hat)` is
#
#     H(z) = a / (1 - (1-a) z^-1)
#
# so the frame rate is part of the answer and dt has to be passed in. At
# 31 FPS the difference from the continuous form is small; at the 3.9 FPS
# MediaPipe falls to while re-detecting, it is not.

def response(cutoff, dt, freqs):
    """(gain, phase-radians) per frequency for the filter frozen at `cutoff`."""
    a = hf._OneEuro._alpha(cutoff, dt)
    out = []
    for f in freqs:
        z = cmath.exp(-1j * 2.0 * math.pi * f * dt)
        h = a / (1.0 - (1.0 - a) * z)
        out.append((abs(h), cmath.phase(h)))
    return out


def legacy_alpha(tau, dt):
    """The fixed per-frame weight that gives `tau` at this frame rate.

    teleop_app applied a constant; this recovers which constant, so the
    baseline curve is the one that was really flying rather than a
    time-based filter wearing its name.
    """
    return 1.0 - math.exp(-dt / tau)


def legacy_response(alpha, dt, freqs):
    out = []
    for f in freqs:
        z = cmath.exp(-1j * 2.0 * math.pi * f * dt)
        h = alpha / (1.0 - (1.0 - alpha) * z)
        out.append((abs(h), cmath.phase(h)))
    return out


# ---- operating points, measured ------------------------------------------

def speeds(path):
    """Per-axis median and p95 of |dx_hat|, from the real filter on a recording.

    Runs `_OneEuro` frame by frame with the same dt clamp the live path
    uses, and reads the speed estimate the filter itself formed - which is
    the quantity beta multiplies, not the raw frame-to-frame difference.
    """
    rows = list(csv.DictReader(open(path)))
    ts = [float(r["t"]) for r in rows]
    med, p95 = [], []
    for a in AXES:
        col = [float(r["tgt_" + a]) for r in rows]
        f, last, seen = hf._OneEuro(), None, []
        for x, t in zip(col, ts):
            dt = hf.DT_MIN if last is None else min(
                max(t - last, hf.DT_MIN), hf.DT_MAX)
            last = t
            f.update(x, dt)
            seen.append(abs(f.dx_hat))
        seen.sort()
        med.append(seen[len(seen) // 2])
        p95.append(seen[int(len(seen) * 0.95)])
    return statistics.median(med), statistics.median(p95)


def median_dt(paths):
    d = []
    for p in paths:
        ts = [float(r["t"]) for r in csv.DictReader(open(p))]
        d += [b - a for a, b in zip(ts, ts[1:])]
    return statistics.median(d)


# ---- svg -----------------------------------------------------------------
#
# Hand-rolled because the alternative is a dependency, and the shapes here
# are two axes and five polylines. Theme-aware: these get read on a dark
# vault as often as a light one, and an SVG that assumes a white page
# turns into black text on black.

W = 960
L, R, T = 66, 26, 74
PLOT_BOTTOM = 388                 # the x-axis; everything under it is text
WRAP = 155                        # characters per note line at 11px in 868 px

STYLE = """
  :root {
    --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --ink3: #86847d;
    --grid: #e6e5e0; --axis: #b9b7af;
    --s1: #86b6ef; --s2: #3987e5; --s3: #1c5cab; --s4: #0d366b;
    --old: #eb6834;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --ink3: #8d8b81;
      --grid: #2e2e2c; --axis: #4a4945;
      --s1: #9ec5f4; --s2: #6da7ec; --s3: #2a78d6; --s4: #184f95;
      --old: #d95926;
    }
  }
  text { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; }
  .title { font-size: 17px; font-weight: 600; fill: var(--ink); }
  .sub   { font-size: 12px; fill: var(--ink2); }
  .note  { font-size: 11px; fill: var(--ink3); }
  .tick  { font-size: 11px; fill: var(--ink2); }
  .axlab { font-size: 12px; fill: var(--ink2); }
  .leg   { font-size: 11.5px; fill: var(--ink); }
  .legd  { font-size: 11.5px; fill: var(--ink2); }
  .grid  { stroke: var(--grid); stroke-width: 1; }
  .axis  { stroke: var(--axis); stroke-width: 1; }
  .ref   { stroke: var(--axis); stroke-width: 1; stroke-dasharray: 2 3; }
  .curve { fill: none; stroke-width: 2; stroke-linejoin: round;
           stroke-linecap: round; }
"""


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Svg:
    def __init__(self):
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, cls="tick", anchor="start", extra=""):
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" '
                 f'text-anchor="{anchor}"{extra}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, cls):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                 f'y2="{y2:.1f}" class="{cls}"/>')

    def path(self, pts, color, dash=None):
        d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" class="curve" stroke="{color}"{da}/>')

    def out(self, title, desc, h):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
                f'height="{h}" viewBox="0 0 {W} {h}" role="img">'
                f"<title>{esc(title)}</title><desc>{esc(desc)}</desc>"
                f"<style>{STYLE}</style>"
                f'<rect width="{W}" height="{h}" fill="var(--surface)"/>'
                + "".join(self.parts) + "</svg>")


def fmt_hz(f):
    return f"{f:g}"


def wrap(notes, width=WRAP):
    """Greedy word wrap. There is no text metric here, so the width is a
    character count calibrated against the widest line that fits."""
    out = []
    for n in notes:
        line = ""
        for word in n.split():
            if line and len(line) + 1 + len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out


def draw(path, title, sub, curves, ylab, yticks, ylo, yhi, value_of, notes,
         fmin, fmax, xticks, nyquist, refs=()):
    """One panel. `value_of` maps a (gain, phase) pair to the plotted y."""
    s = Svg()
    lines = wrap(notes)
    height = PLOT_BOTTOM + 114 + 15 * len(lines) + 18
    px0, px1 = L, W - R
    py0, py1 = T, PLOT_BOTTOM

    def X(f):
        return px0 + (math.log10(f) - math.log10(fmin)) / (
            math.log10(fmax) - math.log10(fmin)) * (px1 - px0)

    def Y(v):
        return py1 - (v - ylo) / (yhi - ylo) * (py1 - py0)

    s.text(L, 30, title, "title")
    s.text(L, 50, sub, "sub")

    for v in yticks:
        s.line(px0, Y(v), px1, Y(v), "grid")
        s.text(px0 - 9, Y(v) + 4, f"{v:g}", "tick", "end")
    for f in xticks:
        s.line(X(f), py0, X(f), py1, "grid")
        s.text(X(f), py1 + 18, fmt_hz(f), "tick", "middle")

    for v, label in refs:
        s.line(px0, Y(v), px1, Y(v), "ref")
        s.text(px0 + 5, Y(v) - 5, label, "note")

    # Nyquist: the response is only defined this far. Beyond it the camera
    # is not reporting a sine, it is reporting an alias of one.
    s.line(X(nyquist), py0, X(nyquist), py1, "ref")
    s.text(X(nyquist) - 6, py0 + 14, f"Nyquist {nyquist:.1f} Hz", "note", "end")

    s.line(px0, py0, px0, py1, "axis")
    s.line(px0, py1, px1, py1, "axis")
    s.text(px0, py1 + 40, "input frequency (Hz, log)", "axlab")
    s.text(16, (py0 + py1) / 2, ylab, "axlab", "middle",
           extra=f' transform="rotate(-90 16 {(py0 + py1) / 2:.1f})"')

    freqs = [fmin * (fmax / fmin) ** (i / 400.0) for i in range(401)]
    for c in curves:
        pts = []
        for f, gp in zip(freqs, c["resp"](freqs)):
            v = value_of(gp)
            pts.append((X(f), Y(min(max(v, ylo), yhi))))
        s.path(pts, c["color"], c.get("dash"))

    # Legend below the axis: five series is one past the count that can be
    # direct-labelled without collisions, and every curve here is the same
    # shape, so a shared key beats five arrows into a bundle of lines.
    ly = py1 + 62
    for i, c in enumerate(curves):
        cx = L + (i % 3) * 290
        cy = ly + (i // 3) * 21
        dash = f' stroke-dasharray="{c["dash"]}"' if c.get("dash") else ""
        s.add(f'<line x1="{cx}" y1="{cy - 4}" x2="{cx + 22}" y2="{cy - 4}" '
              f'class="curve" stroke="{c["color"]}"{dash}/>')
        s.text(cx + 30, cy, c["name"], "leg")
        s.text(cx + 30 + 7.2 * len(c["name"]), cy, c["why"], "legd")

    for i, n in enumerate(lines):
        s.text(L, ly + 52 + i * 15, n, "note")

    with open(path, "w") as fh:
        fh.write(s.out(title, sub, height))
    return path


def main():
    dt = median_dt([STILL, MOVING])
    nyq = 0.5 / dt
    s_med, _ = speeds(STILL)
    m_med, m_p95 = speeds(MOVING)

    fc_still = MINCUTOFF + BETA * s_med
    fc_move = MINCUTOFF + BETA * m_med
    fc_fast = MINCUTOFF + BETA * m_p95
    a_old = legacy_alpha(LEGACY_TAU, dt)
    fc_old = 1.0 / (2.0 * math.pi * LEGACY_TAU)

    curves = [
        dict(name=f"{MINCUTOFF:g} Hz", why="  mincutoff, speed 0",
             color="var(--s1)", resp=lambda fs: response(MINCUTOFF, dt, fs)),
        dict(name=f"{fc_still:.2f} Hz", why="  still.csv, median speed",
             color="var(--s2)", resp=lambda fs: response(fc_still, dt, fs)),
        dict(name=f"{fc_move:.2f} Hz", why="  moving.csv, median speed",
             color="var(--s3)", resp=lambda fs: response(fc_move, dt, fs)),
        dict(name=f"{fc_fast:.2f} Hz", why="  moving.csv, p95 speed",
             color="var(--s4)", resp=lambda fs: response(fc_fast, dt, fs)),
        dict(name=f"{fc_old:.2f} Hz", why="  old EMA, same at every speed",
             color="var(--old)", dash="5 4",
             resp=lambda fs: legacy_response(a_old, dt, fs)),
    ]
    speeds_note = (f"speeds, from the filter's own estimate: still median"
                   f" {s_med:.0f}, moving median {m_med:.0f}, moving p95"
                   f" {m_p95:.0f} counts/s. cutoff = {MINCUTOFF:g} +"
                   f" {BETA} x speed.")

    fmin, fmax = 0.02, nyq
    xticks = [0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]
    sub = (f"one-euro, frozen at four operating points, at the recordings' "
           f"median {dt * 1000:.0f} ms frame ({1 / dt:.1f} FPS)")

    def db(gp):
        return 20.0 * math.log10(max(gp[0], 1e-6))

    draw(os.path.join(HERE, "response_gain.svg"),
         "How much of a shaking finger survives the filter",
         sub, curves, "gain (dB)",
         [-48, -42, -36, -30, -24, -18, -12, -6, 0], -50, 4, db,
         [speeds_note,
          "beta is the whole design: still, the filter is nearly frozen (tau"
          f" {1 / (2 * math.pi * MINCUTOFF):.1f} s); at the p95 of moving.csv it"
          f" has opened {fc_fast / MINCUTOFF:.0f}x, past the fixed EMA it replaces.",
          "The deadband stage is not on this plot and cannot be: a sine under"
          " the threshold comes out as silence, not as an attenuated sine."],
         fmin, fmax, xticks, nyq, refs=[(-3.0, "-3 dB")])

    def deg(gp):
        return math.degrees(gp[1])

    draw(os.path.join(HERE, "response_phase.svg"),
         "How late it comes out",
         sub, curves, "phase (degrees)",
         [0, -15, -30, -45, -60, -75, -90], -92, 4, deg,
         [speeds_note,
          "Read against the gain plot, not alone. At 1 Hz the 0.05 Hz curve lags"
          f" {-math.degrees(response(MINCUTOFF, dt, [1.0])[0][1]):.0f} deg ="
          f" {-math.degrees(response(MINCUTOFF, dt, [1.0])[0][1]) / 360 * 1000:.0f}"
          " ms, but has also cut that 1 Hz component by"
          f" {-20 * math.log10(response(MINCUTOFF, dt, [1.0])[0][0]):.0f} dB:"
          " what arrives late is nearly nothing.",
          "-45 deg at the cutoff for the slow curves, -37 deg for the 2.80 Hz one,"
          " whose cutoff is no longer small against a 31 FPS frame. The lag peaks"
          " (-82 deg at worst) and returns to zero at Nyquist, where this discrete"
          " filter's response is real: the continuous prototype's -90 deg asymptote"
          " is not what the code does.",
          "A per-frequency phase delay, NOT the step lag the parameters were chosen"
          " on (69 ms; see the module docstring). The two answer different"
          " questions and only the second was measured against a moving hand."],
         fmin, fmax, xticks, nyq)

    print(f"dt median {dt * 1000:.1f} ms ({1 / dt:.2f} FPS), Nyquist {nyq:.2f} Hz")
    print(f"speed: still median {s_med:.0f}, moving median {m_med:.0f},"
          f" moving p95 {m_p95:.0f} counts/s")
    print(f"cutoff: {MINCUTOFF:g} / {fc_still:.3f} / {fc_move:.3f} /"
          f" {fc_fast:.3f} Hz    old EMA {fc_old:.2f} Hz (alpha {a_old:.3f})")
    print("wrote response_gain.svg, response_phase.svg")


if __name__ == "__main__":
    main()
