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
    instrument, the sample changed and the statistics are not comparable --
    that is failure mode 2 and it is the single most useful field here.
    """

    n: int
    mean: float
    p10: float
    p50: float
    p90: float
    dark_share: float   # fraction below `dark_below`
    saturation: float   # median, 0-100
    grain: float        # median |luma delta| between adjacent sampled pixels
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
    both sides -- grain is a neighbour statistic, so a different step measures a
    different distance and the numbers stop meaning the same thing.
    """
    px = image.convert("RGB").load()
    w, h = image.size

    vals: list[float] = []
    sats: list[float] = []
    grains: list[float] = []
    grid_vals: list[list[float]] = [[] for _ in range(16)]

    for y in range(0, h, step):
        prev: float | None = None
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
                grains.append(abs(v - prev))
            prev = v

    if not vals:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    vals.sort()
    n = len(vals)
    means = [st.mean(c) for c in grid_vals if len(c) > 30]
    form = st.pstdev(means) if len(means) > 2 else 0.0
    grain = st.median(grains) if grains else 0.0

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


def isolate(
    image: "Image.Image",
    percentile: float = 0.72,
    floor: float = 45.0,
    min_pixels: int = 200,
    downscale: int = 4,
) -> set[tuple[int, int]] | None:
    """Find the largest bright connected region and return its pixel set.

    This exists because measuring a moving object with a fixed rectangle mixes
    object with background by a different amount in every frame -- failure mode
    1. Two crops of the same build reported `mean 86.4` and `mean 157.9` for
    the same element before this function existed.

    Returns None when nothing large enough is found, which is a real answer:
    "there is no object here" beats a number measured on background.
    """
    w, h = image.size
    sw, sh = max(1, w // downscale), max(1, h // downscale)
    small = image.convert("RGB").resize((sw, sh), Image.LANCZOS)
    sp = small.load()

    lum = [[luma(sp[x, y]) for x in range(sw)] for y in range(sh)]
    flat = sorted(v for row in lum for v in row)
    threshold = max(floor, flat[int(len(flat) * percentile)])

    seen = [[False] * sw for _ in range(sh)]
    best: list[tuple[int, int]] = []

    for y0 in range(sh):
        for x0 in range(sw):
            if seen[y0][x0] or lum[y0][x0] < threshold:
                continue
            comp: list[tuple[int, int]] = []
            queue = deque([(x0, y0)])
            seen[y0][x0] = True
            while queue:
                x, y = queue.popleft()
                comp.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < sw and 0 <= ny < sh and not seen[ny][nx]:
                        if lum[ny][nx] >= threshold:
                            seen[ny][nx] = True
                            queue.append((nx, ny))
            if len(comp) > len(best):
                best = comp

    if len(best) < min_pixels:
        return None

    return {
        (x * downscale + ox, y * downscale + oy)
        for x, y in best
        for ox in range(downscale)
        for oy in range(downscale)
        if x * downscale + ox < w and y * downscale + oy < h
    }


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
    and can be the reverse -- failure mode 2.
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
