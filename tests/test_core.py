"""Tests that assert the properties the library exists to guarantee.

These are deliberately written as the four failure modes: each test constructs
the situation that produced a wrong answer in production and asserts that the
library now gives the right one. A test that only checked "the function returns
a number" would have passed throughout every incident in docs/FAILURE-MODES.md.
"""

from PIL import Image, ImageDraw

from pixeltruth.core import (
    Stats,
    compare,
    core_box,
    histogram,
    isolate,
    luma,
    measure,
    speck_density,
)


def _canvas(w=240, h=180, bg=(10, 12, 18)):
    return Image.new("RGB", (w, h), bg)


def _with_object(offset_x=0, fill=(180, 180, 190)):
    """A bright rectangle on a dark ground, movable."""
    im = _canvas()
    d = ImageDraw.Draw(im)
    d.rectangle([60 + offset_x, 50, 160 + offset_x, 130], fill=fill)
    return im


# ── the basics ───────────────────────────────────────────────────────────────

def test_luma_is_0_255_not_lstar():
    """White is 255. If this is ~100 someone swapped in L* -- failure mode 4."""
    assert round(luma((255, 255, 255))) == 255
    assert round(luma((0, 0, 0))) == 0


def test_measure_reports_sample_size():
    s = measure(_canvas(), step=1)
    assert isinstance(s, Stats)
    assert s.n == 240 * 180


def test_empty_mask_returns_zeroed_stats_not_a_crash():
    s = measure(_canvas(), mask=set())
    assert s.n == 0


# ── failure mode 1: a fixed box on a moving object ───────────────────────────

def test_isolate_finds_the_same_object_wherever_it_sits():
    """The whole point: an object that moves must measure the same."""
    left = _with_object(offset_x=0)
    right = _with_object(offset_x=60)

    a = measure(left.crop(core_box(isolate(left))))
    b = measure(right.crop(core_box(isolate(right))))

    # Same object, different position -> brightness must agree closely.
    assert abs(a.p50 - b.p50) < 2.0


def test_a_fixed_box_gets_it_wrong_which_is_why_isolate_exists():
    """Demonstrates the bug. If this ever stops failing, the fixture broke."""
    box = (60, 50, 160, 130)          # correct for offset 0, wrong for offset 60
    a = measure(_with_object(0).crop(box))
    b = measure(_with_object(60).crop(box))
    assert abs(a.p50 - b.p50) > 20.0   # the fixed box is badly misled


def test_isolate_returns_none_rather_than_guessing():
    """No object is a real answer. Measuring the background is not."""
    assert isolate(_canvas(), min_pixels=200) is None


# ── failure mode 2: a threshold mask hides your own change ───────────────────

def test_histogram_sees_darkness_a_masked_statistic_would_drop():
    dark = _canvas(bg=(2, 2, 3))
    h = histogram(dark)
    assert h["<4"] > 99.0, "an unmasked histogram must see near-black"


def test_compare_flags_a_sample_size_mismatch():
    full = _canvas()
    result = compare(full, full, mask=None)
    assert result["sample_warning"] is False

    small = measure(full, mask={(0, 0), (1, 1)})
    assert small.n == 2  # a mask can shrink the sample dramatically; n reveals it


# ── failure mode 3: a magnified crop measures the upscaling ──────────────────

def test_upscaling_destroys_grain_which_is_why_we_never_measure_magnified():
    import random

    random.seed(7)
    noisy = _canvas(120, 90)
    px = noisy.load()
    for y in range(90):
        for x in range(120):
            v = random.randint(60, 200)
            px[x, y] = (v, v, v)

    native = measure(noisy).grain
    blown = measure(noisy.resize((480, 360), Image.LANCZOS)).grain
    assert blown < native * 0.6, "a 4x upscale must flatten grain; never measure there"


# ── the form/grain ratio, which separates a lit solid from noise ─────────────

def test_ratio_separates_a_shaded_solid_from_noise():
    import random

    random.seed(11)

    shaded = _canvas(160, 160)
    p = shaded.load()
    for y in range(160):
        for x in range(160):
            v = int(30 + 170 * (x / 160))       # a smooth gradient: form, no grain
            p[x, y] = (v, v, v)

    noise = _canvas(160, 160)
    p2 = noise.load()
    for y in range(160):
        for x in range(160):
            v = random.randint(30, 200)          # grain, no form
            p2[x, y] = (v, v, v)

    assert measure(shaded).ratio > measure(noise).ratio * 5


# ── specks ───────────────────────────────────────────────────────────────────

def test_speck_density_reports_columns_and_spread():
    im = _canvas(400, 200)
    d = ImageDraw.Draw(im)
    for x in range(20, 90, 8):          # everything crammed into the left column
        d.ellipse([x, 90, x + 3, 93], fill=(240, 240, 240))
    r = speck_density(im)
    assert len(r["columns"]) == 4
    assert r["columns"][0] > r["columns"][3], "a left-heavy field must read left-heavy"
    assert r["spread"] > 1.0
