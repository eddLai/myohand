"""Run sweep_probe with a stub camera and no display.

The drawing code is the part most likely to crash and the part hardest to
reach without a person in front of a lens, so the camera is replaced with
blank frames and the window with a counter. MediaPipe finds no hand in a
blank frame, which exercises the panel, the angle bar and the progress bar
along the no-hand path, then 's' walks through all eight targets to reach
the report. What this cannot reach is the skeleton overlay and the live
marker, which need a real hand.
"""
import sys, types, numpy as np, cv2

frames = [0]
class Cap:
    def __init__(self, *a): pass
    def set(self, *a): pass
    def isOpened(self): return True
    def read(self):
        frames[0] += 1
        return True, np.full((720, 1280, 3), 30, np.uint8)
    def release(self): pass

cv2.VideoCapture = Cap
cv2.namedWindow = lambda *a, **k: None
shown = [0]
def imshow(win, img):
    shown[0] += 1
    assert img.ndim == 3 and img.shape[2] == 3, img.shape
cv2.imshow = imshow
cv2.destroyAllWindows = lambda: None
cv2.waitKey = lambda *a: ord("s")      # skip every target

sys.argv = ["sweep_probe.py", "0"]
import runpy
try:
    runpy.run_path("sweep_probe.py", run_name="__main__")
except SystemExit as e:
    print("exited with: %s" % e)
print("frames read %d, frames drawn %d" % (frames[0], shown[0]))
