"""The client half of the latency ruler, and a reader for what it writes.

handd measures everything from the moment a target arrives; only the
client can see what happened before that - when the frame was grabbed and
when the targets came out of the mapping. Stamps carries those two
numbers and hands them over with the pose.

    stamps = Stamps()
    frame = camera.read();      stamps.frame()
    targets = mapping(frame);   stamps.mapped()
    client.target(targets, stamps)

CLOCK_MONOTONIC on both sides of a unix socket on one host is the same
clock, so no synchronisation is involved and no offset has to be
estimated.

As a tool it summarises the CSV the daemon writes:

    python3 hand_latency.py /tmp/handd_latency.csv

which is the comparison the DC question turns on - the same columns for
the disconnect run and the sync0 run, so the two are read off one ruler.
"""
import sys
import time

STAGES = ["vision", "send", "ipc", "queue", "wire", "exec", "move", "total"]

STAGE_MEANING = {
    "vision": "frame grabbed -> targets computed",
    "send":   "targets computed -> written to the socket",
    "ipc":    "socket write -> parsed by the daemon",
    "queue":  "parsed -> reaches the PDO buffer",
    "wire":   "reaches the buffer -> the frame is sent",
    "exec":   "frame sent -> the execution disconnect (disconnect trigger only)",
    "move":   "frame sent -> ANGLEACT starts changing",
    "total":  "frame grabbed -> ANGLEACT starts changing",
}


def now_ns():
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


class Stamps:
    """Carries the two timestamps only the client can take."""

    __slots__ = ("t_frame", "t_map")

    def __init__(self):
        self.t_frame = 0
        self.t_map = 0

    def frame(self):
        """The camera just handed over a frame."""
        self.t_frame = now_ns()
        self.t_map = 0
        return self

    def mapped(self):
        """The targets for that frame are ready."""
        self.t_map = now_ns()
        return self

    def as_tokens(self):
        """The trailing tokens of a `target` line. Empty when unstamped,
        so an uninstrumented client costs nothing."""
        if not self.t_frame:
            return ""
        return (f" t_frame={self.t_frame} t_map={self.t_map or self.t_frame}"
                f" t_send={now_ns()}")


# ---- reading what the daemon wrote --------------------------------------

def read_csv(path):
    rows = []
    with open(path) as f:
        header = f.readline().strip().split(",")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            for k, v in row.items():
                if k.endswith("_us") or k in ("id", "moved"):
                    try:
                        row[k] = int(v)
                    except ValueError:
                        pass
            rows.append(row)
    return rows


def percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    return s[(len(s) - 1) * pct // 100]


def summarise(rows):
    """Group by trigger, because comparing the two is the whole point."""
    out = {}
    for row in rows:
        out.setdefault(row.get("trigger", "?"), []).append(row)
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1])
        print("usage: hand_latency.py <latency.csv>")
        return 1
    rows = read_csv(argv[1])
    if not rows:
        print(f"{argv[1]}: no complete rows yet")
        return 1
    for trigger, group in summarise(rows).items():
        moved = [r for r in group if r.get("moved")]
        print(f"\n=== trigger: {trigger} "
              f"({len(group)} steps, {len(moved)} reached the hand) ===")
        print(f"{'stage':8s} {'p50':>9s} {'p95':>9s} {'max':>9s}   what it covers")
        for stage in STAGES:
            vals = [r[f"{stage}_us"] for r in group
                    if isinstance(r.get(f"{stage}_us"), int)
                    and r[f"{stage}_us"] >= 0]
            if not vals:
                print(f"{stage:8s} {'-':>9s} {'-':>9s} {'-':>9s}   "
                      f"{STAGE_MEANING[stage]} (did not apply)")
                continue
            print(f"{stage:8s} {percentile(vals, 50):9d} "
                  f"{percentile(vals, 95):9d} {max(vals):9d}   "
                  f"{STAGE_MEANING[stage]}")
        if len(moved) < len(group):
            print(f"  note: {len(group) - len(moved)} step(s) never showed "
                  f"motion within the daemon's timeout - those contribute to "
                  f"every column except move and total")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
