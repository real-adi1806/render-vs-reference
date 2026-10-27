"""Instruments for comparing a rendered page against a reference image.

Every function here takes the SAME arguments for both sides of a comparison.
That is not a style choice. A statistic produced by a different method on each
side is not a comparison, and this module exists because that mistake is
extremely easy to make and produces confident, precise, wrong numbers.

See docs/FAILURE-MODES.md for the four ways these measurements lie, each of
which was observed in production before it was written down.
"""

from __future__ import annotations

import math
import statistics as st
from collections import deque
from dataclasses import dataclass, asdict
from typing import Iterable, Sequence

try:
    from PIL import Image, ImageFilter, ImageChops
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pixel-truth needs Pillow:  pip install pillow") from exc


# Rec.709 luma. Note this is LUMA on a 0-255 scale, not CIE L*, which caps at
# 100. Mixing the two is failure mode 4 and it silently doubles your numbers.
_R, _G, _B = 0.2126, 0.7152, 0.0722


def luma(px: Sequence[int]) -> float:
    """Rec.709 luma of an RGB triple, 0-255."""
    return _R * px[0] + _G * px[1] + _B * px[2]


@dataclass
class Stats:
    """What one instrument measured, including how much it looked at.

    `n` is not decoration. If `n` changes between two runs of the same
    instrument, the sample changed and the statistics are not comparable;
    that is failure mode 2 and it is the single most useful field here.
    """

    n: int
    mean: float
    p10: float
    p50: float
    p90: float
    dark_share: float   # fraction below `dark_below`
    saturation: float   # median, 0-100
    grain: float        # |luma delta| to the neighbour left and above, median per direction, averaged
    form: float         # sd of cell means over a 4x4 grid: variation ACROSS
    ratio: float        # form / grain. A lit solid is >> 1. Noise is << 1.

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"n={self.n:<7d} mean={self.mean:6.1f} p10={self.p10:6.1f} "
            f"p50={self.p50:6.1f} p90={self.p90:6.1f} dark={self.dark_share * 100:5.1f}% "
            f"sat={self.saturation:5.1f} grain={self.grain:5.2f} form={self.form:5.1f} "
            f"ratio={self.ratio:6.2f}"
        )


def _cells(width: int, height: int, grid: int = 4):
    for gy in range(grid):
        for gx in range(grid):
            yield (
                gx * width // grid,
                gy * height // grid,
                (gx + 1) * width // grid,
                (gy + 1) * height // grid,
            )


def measure(
    image: "Image.Image",
    mask: set[tuple[int, int]] | None = None,
    step: int = 1,
    dark_below: float = 30.0,
) -> Stats:
    """Measure an image, or only the pixels named by `mask`.

    `step` samples every Nth pixel in each axis for speed. Use the SAME step on
    both sides: grain is a neighbour statistic, so a different step measures a
    different distance and the numbers stop meaning the same thing.

    `grain` looks in both directions: the median |delta| to the sampled neighbour
    on the left, and the median |delta| to the one above, averaged. (Looking along
    rows only reads horizontal stripes as perfectly smooth.) A pair only counts if
    both pixels are inside `mask`, and a direction with no pairs is left out of
    the average. Grain is also a property of the image's own pixel grid, so it is
    only comparable between images of the same size.
    """
    px = image.convert("RGB").load()
    w, h = image.size

    vals: list[float] = []
    sats: list[float] = []
    grains_x: list[float] = []   # |delta| to the sampled neighbour on the left
    grains_y: list[float] = []   # |delta| to the sampled neighbour above
    grid_vals: list[list[float]] = [[] for _ in range(16)]

    above: dict[int, float] = {}   # the previous sampled row, masked-out pixels absent
    for y in range(0, h, step):
        prev: float | None = None
        row: dict[int, float] = {}
        for x in range(0, w, step):
            if mask is not None and (x, y) not in mask:
                prev = None
                continue
            p = px[x, y]
            v = luma(p)
            vals.append(v)

            hi, lo = max(p), min(p)
            sats.append(0.0 if hi == 0 else (hi - lo) / hi * 100.0)

            gx = min(3, x * 4 // w)
            gy = min(3, y * 4 // h)
            grid_vals[gy * 4 + gx].append(v)

            if prev is not None:
                grains_x.append(abs(v - prev))
            if x in above:
                grains_y.append(abs(v - above[x]))
            row[x] = v
            prev = v
        above = row

    if not vals:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    vals.sort()
    n = len(vals)
    means = [st.mean(c) for c in grid_vals if len(c) > 30]
    form = st.pstdev(means) if len(means) > 2 else 0.0
    directional = [st.median(g) for g in (grains_x, grains_y) if g]
    grain = st.mean(directional) if directional else 0.0

    return Stats(
        n=n,
        mean=st.mean(vals),
        p10=vals[int(n * 0.10)],
        p50=vals[n // 2],
        p90=vals[int(n * 0.90)],
        dark_share=sum(1 for v in vals if v < dark_below) / n,
        saturation=st.median(sats) if sats else 0.0,
        grain=grain,
        form=form,
        ratio=(form / grain) if grain else 0.0,
    )


def _grow(lum, start, keep) -> list[tuple[int, int]]:
    """Every pixel 4-connected to `start` through pixels where `keep(luma)`."""
    sh, sw = len(lum), len(lum[0])
    seen = set(start)
    queue = deque(start)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < sw and 0 <= ny < sh and (nx, ny) not in seen and keep(lum[ny][nx]):
                seen.add((nx, ny))
                queue.append((nx, ny))
    return list(seen)


def _outline(
    image: "Image.Image",
    percentile: float = 0.72,
    floor: float = 45.0,
    min_pixels: int = 200,
    downscale: int = 4,
    min_contrast: float = 1.0,
    max_share: float = 0.5,
) -> tuple[set[tuple[int, int]] | None, bool]:
    """`isolate()`, plus whether the outline was drawn against the ground.

    False means it fell back to a brightness cutoff, which the command line
    reports, because that outline moves when the object's brightness does.
    """
    w, h = image.size
    sw, sh = max(1, w // downscale), max(1, h // downscale)
    # BOX, not LANCZOS: a sharp filter rings at the object's edge, and the
    # ringing would be read as "differs from the ground".
    small = image.convert("RGB").resize((sw, sh), Image.BOX)
    sp = small.load()
    lum = [[luma(sp[x, y]) for x in range(sw)] for y in range(sh)]

    # Where the object is: the largest bright region.
    flat = sorted(v for row in lum for v in row)
    bright = max(floor, flat[int(len(flat) * percentile)])
    seed: list[tuple[int, int]] = []
    claimed: set[tuple[int, int]] = set()
    for y0 in range(sh):
        for x0 in range(sw):
            if (x0, y0) in claimed or lum[y0][x0] < bright:
                continue
            comp = _grow(lum, [(x0, y0)], lambda v: v >= bright)
            claimed.update(comp)
            if len(comp) > len(seed):
                seed = comp
    if not seed:
        return None, False

    # How far it extends: everything connected to it that differs from the
    # ground, lighter or darker, by more than the ground's own noise.
    ring = (
        [lum[0][x] for x in range(sw)] + [lum[sh - 1][x] for x in range(sw)]
        + [lum[y][0] for y in range(sh)] + [lum[y][sw - 1] for y in range(sh)]
    )
    ground = st.median(ring)
    noise = 1.4826 * st.median(abs(v - ground) for v in ring)
    margin = max(min_contrast, 3 * noise)
    grown = _grow(lum, seed, lambda v: abs(v - ground) > margin)

    from_ground = len(grown) <= max_share * sw * sh
    region = grown if from_ground else seed
    if len(region) < min_pixels:
        return None, from_ground

    mask = {
        (x * downscale + ox, y * downscale + oy)
        for x, y in region
        for ox in range(downscale)
        for oy in range(downscale)
        if x * downscale + ox < w and y * downscale + oy < h
    }
    return mask, from_ground


def isolate(
    image: "Image.Image",
    percentile: float = 0.72,
    floor: float = 45.0,
    min_pixels: int = 200,
    downscale: int = 4,
    min_contrast: float = 1.0,
    max_share: float = 0.5,
) -> set[tuple[int, int]] | None:
    """Find the main object and return its pixel set.

    This exists because measuring a moving object with a fixed rectangle mixes
    object with background by a different amount in every frame (failure mode
    1). Two crops of the same build reported `mean 86.4` and `mean 157.9` for
    the same element before this function existed.

    Two steps. The largest region brighter than `floor` and the `percentile`
    of the frame says where the object is. Its outline is then grown through
    every connected pixel that differs from the ground (the median of the
    frame's border), lighter or darker, by more than three robust standard
    deviations of the border, and at least `min_contrast`. A shadow touching
    the object therefore counts as part of it.

    The outline must not come from a brightness cutoff. The first version drew
    it with one, so a uniformly darker copy of the same object kept fewer of
    its own pixels, its box shrank toward the highlight, and a different,
    brighter region was measured: 0.64x mean and 0.49x form for a true 0.6x.
    That is failure mode 2, inside the instrument built against mode 1.

    If the grown outline covers more than `max_share` of the frame, the ground
    is too uneven to tell from the object, and the bright region alone is
    returned. That outline does depend on brightness.

    Returns None when nothing large enough is found, which is a real answer:
    "there is no object here" beats a number measured on background.
    """
    return _outline(image, percentile, floor, min_pixels, downscale, min_contrast, max_share)[0]


def core_box(mask: Iterable[tuple[int, int]], inset: float = 0.25) -> tuple[int, int, int, int]:
    """The middle of a mask's bounding box.

    Eroding to the core drops the antialiased rim and any background caught at
    the edges of the blob, which otherwise pulls every statistic toward the
    ground behind the object.
    """
    pts = list(mask)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    mx, my = (x1 - x0) * inset, (y1 - y0) * inset
    return int(x0 + mx), int(y0 + my), int(x1 - mx), int(y1 - my)


def histogram(image: "Image.Image", bins: Sequence[float] = (4, 10, 15, 20, 30, 50, 100)) -> dict:
    """Share of EVERY pixel in each luma band. No mask, no threshold.

    Run this before concluding anything about tone. A masked percentile tells
    you about the pixels that survived the mask, which is not the same thing
    and can be the reverse (failure mode 2).
    """
    px = image.convert("RGB").load()
    w, h = image.size
    counts = [0] * (len(bins) + 1)
    for y in range(h):
        for x in range(w):
            v = luma(px[x, y])
            slot = len(bins)
            for i, edge in enumerate(bins):
                if v < edge:
                    slot = i
                    break
            counts[slot] += 1
    total = max(1, w * h)
    labels = [f"<{bins[0]:g}"]
    labels += [f"{bins[i]:g}-{bins[i + 1]:g}" for i in range(len(bins) - 1)]
    labels += [f">{bins[-1]:g}"]
    return {label: round(100 * c / total, 2) for label, c in zip(labels, counts)}


def speck_density(image: "Image.Image", above: float = 12.0, window: int = 21, columns: int = 4) -> dict:
    """Small bright features per megapixel, in vertical columns.

    Uses a local MEDIAN, not a small ring with a low threshold. A ring-based
    detector counts deliberate fine grain as particles and reported a number
    45x too high the first time it was used.
    """
    grey = image.convert("L")
    med = grey.filter(ImageFilter.MedianFilter(size=window))
    mask = ImageChops.subtract(grey, med).point(lambda v: 255 if v > above else 0)
    px = mask.load()
    w, h = mask.size
    cols = [0] * columns
    for y in range(h):
        for x in range(w):
            if px[x, y]:
                cols[min(columns - 1, x * columns // w)] += 1
    per_col_mpx = (w / columns) * h / 1e6
    values = [round(c / per_col_mpx) for c in cols]
    total = round(sum(cols) / (w * h / 1e6))
    lo, hi = min(values) or 1, max(values)
    return {"columns": values, "total_per_mpx": total, "spread": round(hi / lo, 2)}


def compare(reference: "Image.Image", candidate: "Image.Image", **kwargs) -> dict:
    """Run one instrument over both sides and report the gap.

    Both images are measured by the same code with the same arguments. That is
    the entire point of this function existing rather than you calling
    `measure` twice and hoping.
    """
    a = measure(reference, **kwargs)
    b = measure(candidate, **kwargs)
    gap = {}
    for field in ("mean", "p10", "p50", "p90", "saturation", "grain", "form", "ratio"):
        ref_v = getattr(a, field)
        cand_v = getattr(b, field)
        gap[field] = {
            "reference": round(ref_v, 2),
            "candidate": round(cand_v, 2),
            "delta": round(cand_v - ref_v, 2),
            "x": round(cand_v / ref_v, 2) if ref_v else None,
        }
    gap["dark_share"] = {
        "reference": round(a.dark_share * 100, 2),
        "candidate": round(b.dark_share * 100, 2),
        "delta": round((b.dark_share - a.dark_share) * 100, 2),
        "x": None,
    }
    return {
        "reference_n": a.n,
        "candidate_n": b.n,
        # Flagged loudly: a big sample-size difference means the two sides are
        # not the same population and every row below is suspect.
        "sample_warning": abs(a.n - b.n) > 0.25 * max(a.n, b.n, 1),
        "fields": gap,
    }
