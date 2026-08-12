"""Chinese text on an OpenCV frame, and the palette the calibration tools share.

cv2.putText cannot draw CJK at all - it renders every character as a hollow
box - so every Chinese string goes through PIL and back. That round trip is
expensive enough that it is worth batching: collect the strings for a frame
and hand them over in one call rather than one conversion per label.

This lives apart from the tools because there are now two of them, and a
second copy of a font path is a second thing to fix when the font moves.
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CJK_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

WHITE, GREY, DIM = (240, 240, 240), (155, 155, 155), (70, 70, 70)
OK, BAD, HI = (90, 220, 120), (80, 80, 245), (255, 175, 55)

_fonts = {}


def cjk_font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(CJK_PATH, size)
    return _fonts[size]


def draw_cjk(img, items):
    """One PIL round trip for every Chinese string on this frame."""
    if not items:
        return img
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, (x, y), size, col in items:
        d.text((x, y), text, font=cjk_font(size), fill=(col[2], col[1], col[0]))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
