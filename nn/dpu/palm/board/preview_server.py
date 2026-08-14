"""Serve the board's live detection as a web page.

The KD240 has no display, and judging a tracker by counters alone is how the
last hour was spent measuring a camera pointed at an empty room. A person
looking at the overlay would have seen that in one second.

MJPEG over HTTP rather than X forwarding or VNC: it needs nothing installed
on the viewing machine, survives being closed and reopened, and can be
watched from any host on the lab network while the loop keeps running.

    sudo python3 preview_server.py [port]
    then open  http://120.126.83.228:8080

Query the same server for /stats to read the counters as JSON.
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, os.path.expanduser("~/pipe_bench"))
sys.path.append(os.path.expanduser(
    "~/rh56f1_kd240/env/lib/python3.10/site-packages"))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
MODELS = os.path.expanduser("~/rh56f1_kd240/models")

# MediaPipe's hand skeleton, as pairs of landmark indices
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
         (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
         (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]

ORANGE, PURPLE, PINK = (60, 132, 245), (217, 84, 140), (108, 54, 214)

latest = {"jpg": None}
stats = {"fps": 0.0, "flag": 0.0, "held": 0, "frames": 0, "redetects": 0,
         "palm": "DPU"}
lock = threading.Lock()


def worker():
    import hand_pipeline as hp

    pipe = hp.HandPipeline(
        palm=os.path.join(MODELS, "palm_detection_lite.tflite"),
        landmark=os.path.join(MODELS, "hand_landmark_lite.tflite"),
        threads=4)
    try:
        from dpu_palm import DpuPalm
        DpuPalm().attach(pipe.det)
    except Exception as e:                     # fall back rather than go dark
        stats["palm"] = "tflite (%s)" % type(e).__name__
        print("DPU palm unavailable:", e)

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)

    t0, n = time.time(), 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        out = pipe(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        n += 1
        stats["frames"] = n
        stats["redetects"] = pipe.t["redetects"]

        if out is not None:
            stats["held"] += 1
            stats["flag"] = out["flag"]
            pts = np.asarray(out["img"])[:, :2].astype(int)
            for a, b in BONES:
                cv2.line(bgr, tuple(pts[a]), tuple(pts[b]), PURPLE, 2)
            for i, p in enumerate(pts):
                cv2.circle(bgr, tuple(p), 5,
                           ORANGE if i in (4, 8, 12, 16, 20) else PINK, -1)
            if pipe.roi:
                xc, yc, sc, _ = pipe.roi
                h = sc / 2.0
                cv2.rectangle(bgr, (int(xc - h), int(yc - h)),
                              (int(xc + h), int(yc + h)), ORANGE, 2)
        if n % 10 == 0:
            el = time.time() - t0
            stats["fps"] = 10.0 / el if el else 0.0
            t0 = time.time()

        cv2.putText(bgr, "palm:%s  %.1f fps  flag %.2f  held %d/%d  redet %d"
                    % (stats["palm"], stats["fps"], stats["flag"],
                       stats["held"], n, stats["redetects"]),
                    (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (255, 255, 255), 4)
        cv2.putText(bgr, "palm:%s  %.1f fps  flag %.2f  held %d/%d  redet %d"
                    % (stats["palm"], stats["fps"], stats["flag"],
                       stats["held"], n, stats["redetects"]),
                    (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, ORANGE, 2)

        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with lock:
                latest["jpg"] = buf.tobytes()


PAGE = b"""<!doctype html><meta charset=utf-8>
<title>KD240 hand tracking</title>
<style>
 body{margin:0;background:#FBF9F4;font:16px system-ui;color:#2b2b2b;
      display:flex;flex-direction:column;align-items:center}
 h1{font-size:18px;font-weight:600;margin:14px 0 6px}
 img{max-width:100%;border:3px solid #F5843C;border-radius:6px}
</style>
<h1>KD240 &mdash; palm on DPU, landmarks on A53</h1>
<img src="/stream">
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
            return
        if self.path == "/stats":
            import json
            b = json.dumps(stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=f")
        self.end_headers()
        try:
            while True:
                with lock:
                    jpg = latest["jpg"]
                if jpg is None:
                    time.sleep(0.05)
                    continue
                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n"
                                 b"Content-Length: %d\r\n\r\n" % len(jpg))
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass


threading.Thread(target=worker, daemon=True).start()
print("open http://120.126.83.228:%d" % PORT)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
