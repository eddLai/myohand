"""Client for handd, the resident EtherCAT master.

The old path was one `hand_set` process per pose. This one connects once
and pushes targets down a unix socket, so a caller can hold a rate
instead of paying process startup per gesture.

The daemon owns the safety layer, so there is nothing to bypass here and
nothing to re-implement: send the pose you want, read back what the guard
did with it.

    with HandClient() as hand:
        hand.target([1500, 1500, 1500, 1500, 1200, 1500])
        print(hand.state()["ang"])

On connect the client checks the daemon's scale against hand_scale.py
rather than assuming the two were edited together - the target scale is
still an open question (see hand_safety.h) and a silent disagreement
about it would move the hand to the wrong place, quietly.
"""
import json
import os
import socket

import hand_scale

SOCKET_DEFAULT = os.environ.get("HAND_SOCKET", "/tmp/inspire_hand.sock")


class HandDaemonError(RuntimeError):
    pass


class HandClient:
    def __init__(self, path=SOCKET_DEFAULT, timeout=2.0, check_scale=True):
        self.path = path
        self.timeout = timeout
        self.check_scale = check_scale
        self.info = None
        self._sock = None
        self._buf = b""

    # ---- connection ---------------------------------------------------
    def connect(self):
        if self._sock is not None:
            return self
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
        except OSError as e:
            raise HandDaemonError(
                f"no handd on {self.path} ({e.strerror}). Start it with: "
                f"./handd --iface=$ECAT_IFACE") from e
        self._sock = s
        self.info = self.command("hello")
        if self.check_scale:
            theirs = self.info.get("scale", {})
            mine = hand_scale.as_dict()
            bad = [f"{k}: python {mine[k]} vs daemon {theirs.get(k)}"
                   for k in mine if mine[k] != theirs.get(k)]
            if bad:
                raise HandDaemonError(
                    "the daemon and hand_scale.py disagree about what a "
                    "target means - " + "; ".join(bad))
        return self

    def close(self):
        if self._sock is None:
            return
        try:
            self.command("bye")
        except (HandDaemonError, OSError):
            pass
        finally:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---- protocol -----------------------------------------------------
    def command(self, line):
        if self._sock is None:
            self.connect()
        self._sock.sendall(line.encode() + b"\n")
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise HandDaemonError("handd closed the connection")
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\n", 1)
        reply = json.loads(raw.decode())
        if not reply.get("ok"):
            raise HandDaemonError(reply.get("error", "unknown daemon error"))
        return reply

    def hello(self):
        return self.info if self.info else self.command("hello")

    def scale(self):
        return self.command("scale")["scale"]

    def state(self):
        return self.command("state")

    def target(self, targets):
        """Command a 6-axis pose: [pinky, ring, middle, index, thumb_bend,
        thumb_rot]. Out-of-range values are clamped by the daemon, -1
        holds an axis where it is."""
        if len(targets) != 6:
            raise HandDaemonError("need exactly 6 targets")
        return self.command("target " + " ".join(str(int(t)) for t in targets))

    @property
    def simulated(self):
        return bool((self.info or {}).get("simulate"))


if __name__ == "__main__":
    import sys
    args = sys.argv[1:] or ["hello"]
    with HandClient() as hand:
        if args[0] == "target":
            print(json.dumps(hand.target([int(v) for v in args[1:7]])))
        elif args[0] in ("hello", "state", "scale"):
            print(json.dumps(hand.command(args[0]), indent=1))
        else:
            print("usage: hand_client.py [hello|state|scale|"
                  "target P R M I TB TR]")
