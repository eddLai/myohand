#!/usr/bin/env python3
"""Live view of the T1 touch-sensing block, for calibrating field order.

The daemon hands over 34 raw shorts (state()["tac"]). The datasheet says
8 capacitive modules x 4 quantities (normal force, tangential force,
tangential direction, proximity) + 2 unnamed fields, but NOT in which
order they appear on the wire. This tool exists to settle that: press one
known module at a time and watch which columns move.

The 8x4 grid below is therefore a HYPOTHESIS (module-major, quantity-minor),
not a calibrated mapping. Until someone runs the press-one-module session
and records the result, trust the flat indices, not the row/column labels.

    ./tac_view.py                 # against the default daemon socket
    ./tac_view.py --socket=PATH   # against another one
"""
import argparse
import sys
import time

from hand_client import HandClient, SOCKET_DEFAULT

# hypothesis only - see module docstring
MODULES = ["pinky tip", "ring tip", "middle tip", "index tip",
           "thumb tip", "palm A", "palm B", "palm C"]
QUANTITIES = ["normal", "tangential", "direction", "proximity"]


def render(tac, baseline):
    rows = [f"{'idx':>3} {'module?':<11}" +
            "".join(f"{q:>12}" for q in QUANTITIES)]
    for m, name in enumerate(MODULES):
        cells = []
        for q in range(4):
            i = m * 4 + q
            mark = "*" if abs(tac[i] - baseline[i]) > 3 else " "
            cells.append(f"{tac[i]:>10}{mark} ")
        rows.append(f"{m * 4:>3} {name:<11}" + "".join(cells))
    rows.append(f"{32:>3} {'unnamed':<11}{tac[32]:>10}  {tac[33]:>10}")
    rows.append("(* = moved vs the baseline captured at start; "
                "grid layout is an unverified hypothesis)")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default=SOCKET_DEFAULT)
    ap.add_argument("--hz", type=float, default=10.0)
    args = ap.parse_args()

    with HandClient(path=args.socket) as hand:
        st = hand.state()
        tac = st.get("tac")
        if tac is None:
            sys.exit("this hand reports no touch block (tac=null) - "
                     "not a T1, or the daemon predates tactile support")
        baseline = list(tac)
        if st.get("simulate"):
            print("NOTE: daemon is simulated - values are the sim's "
                  "fixed pattern, not a hand\n")
        while True:
            st = hand.state()
            frame = render(st["tac"], baseline)
            print(f"\x1b[2J\x1b[H{frame}", flush=True)
            time.sleep(1.0 / args.hz)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
