"""Where teleop's targets go, and how often.

Three destinations behind one interface, because the right one depends on
whether the daemon is running and whether there is a hand plugged in at
all. It used to depend on an open question - does the firmware need the
disconnect? - which 2026-08-06 answered: no, it needs a feed slow enough
to absorb, and `handd` gives it one.

  daemon    stream into handd at a fixed rate. This is continuous
            following: the pose is pushed as it changes rather than
            waiting for the operator to hold still.
  hand_set  one subprocess per pose, the path that predates the daemon.
            Kept as the fallback when handd is not running; it cycles at
            1 kHz, so what applies its pose is the process exiting.
  dry-run   nothing leaves the process. The vision chain, the mapping and
            the whole UI can be worked on with no hand and no daemon.

The settle gate belongs to the caller, not here, but the default depends
on the sink: streaming into the daemon makes waiting-for-stillness
pointless, while paying two to three seconds per hand_set makes it
essential. settle_frames_default() is where that lives.
"""
import os
import re
import subprocess
import threading
import time

import hand_scale

HERE = os.path.dirname(os.path.abspath(__file__))


class Sink:
    """What teleop needs from a destination."""

    name = "none"
    #: how far a target must move before it is worth resending. A distance
    #: on the target scale, so it moved with the 2026-08-06 correction:
    #: 25 units of the old 0..2000 scale are 12 ANGLEACT counts.
    deadband = 12

    def __init__(self):
        self.busy = False
        self.last_result = "idle"
        self.actual = None        # where the hand says it got to
        self.started = None       # perf_counter of an in-flight send
        self.sent = 0

    def send(self, targets, stamps=None):
        raise NotImplementedError

    def elapsed(self):
        return (time.perf_counter() - self.started) if self.started else None

    def close(self):
        pass


def settle_frames_default(sink_name):
    """Waiting for five still frames exists because a pose used to cost
    two to three seconds. Streaming into the daemon removes the reason."""
    return 0 if sink_name in ("daemon", "none") else 5


class DryRunSink(Sink):
    """Accepts poses and goes nowhere. Records the last one so tests and
    the UI have something honest to show."""

    name = "none"

    def __init__(self):
        super().__init__()
        self.last_target = None
        self.last_result = "dry run - nothing is being sent to a hand"

    def send(self, targets, stamps=None):
        self.last_target = list(targets)
        self.sent += 1
        return True


class HandSetSink(Sink):
    """One `hand_set` process per pose. Slow by construction: it feeds the
    slave at 1 kHz, which this hand applies nothing at, so the pose lands
    when the process exits and the frames stop."""

    name = "hand_set"
    deadband = 120            # a resend costs seconds, so only for real moves
                              # (250 on the pre-2026-08-06 target scale)

    def __init__(self, binary=None, force=500, speed=1000, timeout=40):
        super().__init__()
        self.binary = binary or os.path.join(HERE, "soem_build", "hand_set")
        self.force, self.speed, self.timeout = force, speed, timeout
        self._lock = threading.Lock()
        if not os.path.exists(self.binary):
            raise FileNotFoundError(
                f"{self.binary} is not built - run `make all` in "
                f"{HERE}, or use --sink=none to work without a hand")

    def send(self, targets, stamps=None):
        if not self._lock.acquire(blocking=False):
            return False                      # one pose at a time, by nature
        threading.Thread(target=self._work, args=(list(targets),),
                         daemon=True).start()
        return True

    def _work(self, targets):
        self.busy = True
        try:
            t0 = self.started = time.perf_counter()
            r = subprocess.run(
                [self.binary] + [str(int(v)) for v in targets]
                + [str(self.force), str(self.speed)],
                capture_output=True, text=True, timeout=self.timeout)
            dt = time.perf_counter() - t0
            out = (r.stdout + r.stderr).strip().splitlines()
            self._read_back(out)
            guarded = any("guard" in ln for ln in out)
            self.last_result = (f"held back a clash  {dt:.1f}s" if guarded
                                else f"pose reached the hand  {dt:.1f}s")
            self.sent += 1
        except Exception:
            self.last_result = ("hand did not answer - check its power and "
                                "the RJ45 link")
        finally:
            self.started = None
            self.busy = False
            self._lock.release()

    def _read_back(self, lines):
        """hand_set already prints where the joints ended up; keep it."""
        for ln in lines:
            m = re.search(r"ANG=\[([-\d ]+)\]", ln)
            if m:
                self.actual = [hand_scale.ang_to_target(v)
                               for v in m.group(1).split()]
                return


class DaemonSink(Sink):
    """Streams the latest pose into handd at a fixed rate.

    The camera runs at whatever frame rate it manages and the sender runs
    at its own; decoupling them is the point, so a slow frame does not
    become a gap in the command stream and a fast one does not flood it.

    Only a pose that has actually moved is sent. Repeating an unchanged
    one is free under the continuous trigger, but with watchdog or
    disconnect it schedules an execution cycle for a pose the hand is
    already holding, so the deadband stays.
    """

    name = "daemon"

    def __init__(self, socket_path=None, rate_hz=50, deadband=12,
                 telemetry_hz=5):
        super().__init__()
        import hand_client                      # deferred: only this sink needs it
        self.deadband = deadband
        self.rate_hz = max(1, int(rate_hz))
        self.telemetry_hz = telemetry_hz
        self._client = hand_client.HandClient(
            **({"path": socket_path} if socket_path else {}))
        self._client.connect()
        self.info = self._client.info
        self.last_result = (f"streaming to handd at {self.rate_hz} Hz "
                            f"({self.info.get('trigger')} trigger)")
        if self.info.get("simulate"):
            self.last_result += "  [SIMULATED - no hand is being driven]"
        self._pending = None
        self._last_sent = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    @property
    def simulated(self):
        return bool(self.info.get("simulate"))

    def send(self, targets, stamps=None):
        with self._lock:
            self._pending = (list(targets), stamps)
        return True

    def _moved_enough(self, targets):
        if self._last_sent is None:
            return True
        return max(abs(a - b) for a, b in zip(targets, self._last_sent)) \
            > self.deadband

    def _pump(self):
        period = 1.0 / self.rate_hz
        next_at = time.perf_counter()
        tele_every = max(1, self.rate_hz // max(1, self.telemetry_hz))
        tick = 0
        while not self._stop.is_set():
            next_at += period
            time.sleep(max(0.0, next_at - time.perf_counter()))
            with self._lock:
                item, self._pending = self._pending, None
            try:
                if item is not None:
                    targets, stamps = item
                    if self._moved_enough(targets):
                        r = self._client.target(targets, stamps)
                        self._last_sent = targets
                        self.sent += 1
                        if r.get("guarded"):
                            self.last_result = f"guard: {r['guard_note']}"
                tick += 1
                if tick % tele_every == 0:
                    st = self._client.state()
                    if st.get("bus") == "up":
                        self.actual = [hand_scale.ang_to_target(v)
                                       for v in st["ang"]]
            except Exception as e:                       # noqa: BLE001
                self.last_result = f"handd stopped answering: {e}"
                self._stop.set()

    def stats(self):
        return self._client.stats()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._client.close()


def open_sink(kind, socket_path=None, rate_hz=50, deadband=None,
              force=500, speed=1000):
    """Open the named sink, or explain why it could not be opened.

    Never silently downgrades: asking for the daemon and quietly getting
    hand_set would change what is being measured without saying so.
    """
    if kind == "none":
        return DryRunSink()
    if kind == "hand_set":
        s = HandSetSink(force=force, speed=speed)
    elif kind == "daemon":
        s = DaemonSink(socket_path=socket_path, rate_hz=rate_hz,
                       deadband=deadband if deadband is not None else 12)
    else:
        raise ValueError(f"unknown sink '{kind}' - use daemon, hand_set or none")
    if deadband is not None:
        s.deadband = deadband
    return s
