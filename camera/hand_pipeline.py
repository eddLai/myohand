"""Drive the palm and landmark models directly, in place of mp.solutions.hands.

MediaPipe's Python solution loads these two tflite files itself and decides how
to run them, and one of its decisions is fatal on the KD240: XNNPACK is pinned
to a single thread on a four-core part, with no parameter to say otherwise. On
that board the black box measures 7.8 FPS on recorded frames where this module
measures 19.0, and the only difference is that the thread count is ours to set.
The same handle is what would later let the landmark model run somewhere other
than the CPU.

The cost is owning the code between the two models: padding the frame to square,
turning 2016 anchor offsets into boxes, suppressing all but one, deriving the
rotated crop the landmark model expects, and projecting its answer back. Most of
that is vendored from blaze_app_python (Apache 2.0, camera/blaze/), whose
constants come from MediaPipe's own v0.10.9 graph definitions. Added here is the
tracking path - after a hand is found, the next crop comes from the landmarks
just measured rather than from detecting again - because MediaPipe does that on
all but a handful of frames and the landmarks disagree without it.

Verified against MediaPipe frame by frame on a 150-frame recording: joint angles
agree to 1.2-2.2 degrees at the median, landmarks to 1.3 pixels. What remains is
concentrated in the four frames after a re-detection, where the two sides start
from slightly different crops and converge within about four frames, and in
poses where the thumb is hidden and MediaPipe's own answer is unstable.

`MediaPipeHands` presents the same three attributes teleop reads off a
`hands.process()` result, so switching between the two changes one line.
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "blaze"))
from blazedetector import BlazeDetector
from blazelandmark import BlazeLandmark

MP_MODULES = os.path.join(os.path.dirname(os.__file__), "site-packages",
                          "mediapipe", "modules")

# hand_landmark_landmarks_to_roi.pbtxt takes its box over the joints that stay
# put when fingers move, so a closing fist does not shrink the next crop
BBOX_IDS = [0, 1, 2, 3, 5, 6, 9, 10, 13, 14, 17, 18]


def default_models():
    """Reuse the weights the installed MediaPipe already ships."""
    import mediapipe
    root = os.path.join(os.path.dirname(mediapipe.__file__), "modules")
    return (os.path.join(root, "palm_detection", "palm_detection_lite.tflite"),
            os.path.join(root, "hand_landmark", "hand_landmark_lite.tflite"))


def landmarks_to_roi(pts):
    """Centre, side and angle of the next frame's crop, from 21 image points."""
    xy = pts[:, :2]
    x0, y0 = xy[0]                                   # wrist
    x1, y1 = 0.25 * (xy[5] + xy[13]) + 0.5 * xy[9]   # across the knuckles
    rot = 0.5 * np.pi - np.arctan2(y0 - y1, x1 - x0)
    rot = (rot + np.pi) % (2 * np.pi) - np.pi

    sub = xy[BBOX_IDS]
    mid = 0.5 * (sub.min(0) + sub.max(0))
    c, s = np.cos(rot), np.sin(rot)
    R = np.array(((c, -s), (s, c)))
    proj = (sub - mid) @ R
    lo, hi = proj.min(0), proj.max(0)
    centre = R @ (0.5 * (lo + hi)) + mid
    w, h = hi - lo
    return (centre[0] + 0.1 * h * s, centre[1] - 0.1 * h * c,
            2.0 * max(w, h), rot)


class HandPipeline:
    """Detect once, then follow the hand until the presence score drops."""

    def __init__(self, palm=None, landmark=None, presence=0.5, threads=None):
        if palm is None or landmark is None:
            p, l = default_models()
            palm, landmark = palm or p, landmark or l
        # the thread count is the entire reason this module exists; the vendored
        # loaders build their interpreters with defaults, so the constructor is
        # wrapped for the duration of loading
        import ai_edge_litert.interpreter as lit
        plain = lit.Interpreter
        if threads:
            lit.Interpreter = lambda **kw: plain(num_threads=threads, **kw)
        try:
            self.det = BlazeDetector("blazepalm")
            self.det.load_model(palm)
            self.lm = BlazeLandmark("blazehandlandmark")
            self.lm.load_model(landmark)
        finally:
            lit.Interpreter = plain
        # the vendored wrapper reads three of the four outputs and drops the
        # world skeleton, which is the only one the joint angles are built from
        self.i_world = self.lm.output_details[3]["index"]
        self.presence = presence
        self.roi = None

    def reset(self):
        self.roi = None

    def _detect(self, rgb):
        img, scale, pad = self.det.resize_pad(rgb)
        dets = self.det.predict_on_image(img)
        if len(dets) == 0:
            return None
        dets = self.det.denormalize_detections(dets, scale, pad)
        xc, yc, sc, th = self.det.detection2roi(dets)
        return float(xc[0]), float(yc[0]), float(sc[0]), float(th[0])

    def __call__(self, rgb):
        """One RGB frame in; None while no hand is being followed."""
        redetected = self.roi is None
        if redetected:
            self.roi = self._detect(rgb)
            if self.roi is None:
                return None

        xc, yc, sc, th = self.roi
        crop, affine, _ = self.lm.extract_roi(
            rgb, np.array([xc]), np.array([yc]), np.array([th]), np.array([sc]))
        self.lm.interp_landmark.set_tensor(self.lm.in_idx,
                                           np.expand_dims(crop[0], 0))
        self.lm.interp_landmark.invoke()
        get = self.lm.interp_landmark.get_tensor
        flag = float(np.asarray(get(self.lm.out_flag_idx)).ravel()[0])
        handed = float(np.asarray(get(self.lm.out_handedness_idx)).ravel()[0])
        norm = np.asarray(get(self.lm.out_landmark_idx)).reshape(1, 21, 3) \
            / self.lm.resolution
        world = np.asarray(get(self.i_world)).reshape(21, 3)

        if flag < self.presence:
            self.roi = None          # lost: detect again on the next frame
            return None

        img_pts = self.lm.denormalize_landmarks(norm, affine)[0]
        self.roi = landmarks_to_roi(img_pts)
        return {"img": img_pts, "world": world, "flag": flag,
                "handedness": handed, "redetected": redetected}


class _Classification(object):
    def __init__(self, label, score):
        self.label, self.score = label, score
        self.index = 0 if label == "Left" else 1


class _Handedness(object):
    def __init__(self, label, score):
        self.classification = [_Classification(label, score)]


class _Result(object):
    __slots__ = ("multi_hand_landmarks", "multi_hand_world_landmarks",
                 "multi_handedness")

    def __init__(self, img=None, world=None, handedness=None):
        self.multi_hand_landmarks = img
        self.multi_hand_world_landmarks = world
        self.multi_handedness = handedness


class MediaPipeHands:
    """What `mp.solutions.hands.Hands` returns, from the models run directly.

    Only the three attributes teleop reads are provided. Landmarks are real
    protobuf messages rather than look-alikes so that drawing_utils, which
    inspects them, keeps working.
    """

    def __init__(self, threads=4, presence=0.5, **kw):
        self.pipe = HandPipeline(presence=presence, threads=threads)

    def process(self, rgb):
        from mediapipe.framework.formats import landmark_pb2
        out = self.pipe(rgb)
        if out is None:
            return _Result()
        # normalise against the frame in hand: a camera that ignored the width
        # it was asked for would otherwise skew every landmark
        h, w = rgb.shape[:2]
        img = landmark_pb2.NormalizedLandmarkList()
        for x, y, z in out["img"]:
            img.landmark.add(x=float(x) / w, y=float(y) / h, z=float(z) / w)
        world = landmark_pb2.LandmarkList()
        for x, y, z in out["world"]:
            world.landmark.add(x=float(x), y=float(y), z=float(z))
        # measured against MediaPipe's own labels on 111 frames: the scalar is
        # the probability of the left hand, and its score is reported as read
        # for Left and inverted for Right
        p = out["handedness"]
        label, score = ("Left", p) if p >= 0.5 else ("Right", 1.0 - p)
        return _Result([img], [world], [_Handedness(label, score)])

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
