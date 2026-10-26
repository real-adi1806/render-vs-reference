"""Generate the two demo images the README measures.

    python examples/make_examples.py

lit.png       a close-up of a sphere lit from the top of the frame, filling the
              frame, with a little sensor grain: a real shaded surface.
shuffled.png  the SAME pixels, shuffled with a fixed seed.

The two images contain exactly the same pixel values, so every histogram
statistic (mean, percentiles, saturation, dark share) is identical. One is an
object and one is static. That is the case a histogram cannot see and `ratio`
can. The object fills the frame on purpose: with background in the picture, a
whole-image measurement mixes object and background, which is failure mode 1.
"""
import math
import random
from pathlib import Path

from PIL import Image

SIZE = 192
RADIUS = SIZE * 0.75            # larger than the frame's half-diagonal, so the
CENTRE = SIZE / 2               # whole frame lies on the sphere's surface
BASE = (120, 190, 170)          # a muted teal, so saturation has something to read
LIGHT = (0.0, -0.75, 0.66)      # from the top of the frame, towards the viewer
AMBIENT = 0.18
GRAIN = 3                       # +/- levels of per-pixel noise, like a real sensor
SEED = 7

OUT = Path(__file__).resolve().parent


def _normalise(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def lit_surface():
    light = _normalise(LIGHT)
    rng = random.Random(SEED)
    img = Image.new("RGB", (SIZE, SIZE))
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            dx = (x + 0.5 - CENTRE) / RADIUS
            dy = (y + 0.5 - CENTRE) / RADIUS
            nz = math.sqrt(1.0 - dx * dx - dy * dy)
            lambert = max(0.0, dx * light[0] + dy * light[1] + nz * light[2])
            shade = AMBIENT + (1.0 - AMBIENT) * lambert
            n = rng.randint(-GRAIN, GRAIN)
            px[x, y] = tuple(max(0, min(255, round(c * shade) + n)) for c in BASE)
    return img


def shuffled(img):
    # getdata() is deprecated from Pillow 12 and removed in 14.
    flat = img.get_flattened_data if hasattr(img, "get_flattened_data") else img.getdata
    values = list(flat())
    random.Random(SEED).shuffle(values)
    out = Image.new("RGB", img.size)
    out.putdata(values)
    return out


if __name__ == "__main__":
    lit = lit_surface()
    lit.save(OUT / "lit.png")
    shuffled(lit).save(OUT / "shuffled.png")
    print("wrote", OUT / "lit.png", "and", OUT / "shuffled.png")
