"""Tests that assert the properties the library exists to guarantee.

These are deliberately written as the four failure modes: each test constructs
the situation that produced a wrong answer in production and asserts that the
library now gives the right one. A test that only checked "the function returns
a number" would have passed throughout every incident in docs/FAILURE-MODES.md.
"""

import json
import math

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


def _orb(centre, radius, gain, w=240, h=180, bg=(10, 12, 18)):
    """A sphere lit from the upper left, its brightness scaled by `gain`.

    Its shadow side fades towards the ground, so where its outline is drawn
    depends on any brightness cutoff, which is what the darker-copy tests need.
    """
    im = _canvas(w, h, bg)
    px = im.load()
    lx, ly, lz = -0.45, -0.55, 0.70
    norm = math.sqrt(lx * lx + ly * ly + lz * lz)
    cx, cy = centre
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) / radius, (y - cy) / radius
            d2 = dx * dx + dy * dy
            if d2 > 1:
                continue
            lam = max(0.0, (dx * lx + dy * ly + math.sqrt(1 - d2) * lz) / norm)
            v = int((30 + 215 * lam) * gain)
            px[x, y] = (v, v, v)
    return im


def _run_cli(argv, capsys):
    """Run the command line; return (exit code or message, stdout, stderr)."""
    from pixeltruth.cli import main

    try:
        code = main(argv)
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ── the basics ───────────────────────────────────────────────────────────────

def test_luma_is_0_255_not_lstar():
    """White is 255. If this is ~100 someone swapped in L*: failure mode 4."""
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


def test_isolate_outlines_a_darker_object_just_as_large():
    """The outline is geometry. It must not shrink because the object got darker.

    The orb covers x 60 to 179 and y 30 to 149. The first isolate() lost its
    shadow side, and lost more of it at 0.6x brightness: 10% fewer pixels.
    """
    def box(mask):
        xs = [x for x, _ in mask]
        ys = [y for _, y in mask]
        return min(xs), min(ys), max(xs), max(ys)

    # On the second ground the darker copy's shadow side (18) is darker than
    # the ground (25) while the bright copy's (30) is lighter: the outline
    # has to follow "differs from the ground", not "brighter than it".
    for ground in ((10, 12, 18), (25, 25, 25)):
        bright = isolate(_orb((120, 90), 60, 1.0, bg=ground))
        dark = isolate(_orb((120, 90), 60, 0.6, bg=ground))
        assert abs(len(bright) - len(dark)) <= 0.02 * len(bright), (ground, len(bright), len(dark))
        for edges in (box(bright), box(dark)):
            assert all(abs(a - b) <= 4 for a, b in zip(edges, (60, 30, 179, 149))), (ground, edges)


def test_isolate_measures_the_same_region_of_a_darker_object(tmp_path, capsys):
    """A copy at 0.6x brightness, bigger and elsewhere, must read 0.6x.

    The first isolate() drew the outline with one brightness cutoff, so the
    darker copy kept fewer of its own pixels, its box shrank toward the
    highlight, and the core it measured was a different, brighter part of the
    object. It read about 0.64x mean and 0.49x form for a true 0.6x: failure
    mode 2, inside the instrument built against mode 1.
    """
    ref, cand = tmp_path / "ref.png", tmp_path / "cand.png"
    _orb((80, 95), 50, 1.0).save(ref)
    _orb((160, 85), 64, 0.6).save(cand)

    code, out, _ = _run_cli(["compare", str(ref), str(cand), "--isolate", "--json"], capsys)
    assert code == 0
    fields = json.loads(out)["fields"]
    assert abs(fields["mean"]["x"] - 0.6) <= 0.02, fields["mean"]
    assert abs(fields["form"]["x"] - 0.6) <= 0.03, fields["form"]


# ── failure mode 2: a threshold mask hides your own change ───────────────────

def test_histogram_sees_darkness_a_masked_statistic_would_drop():
    dark = _canvas(bg=(2, 2, 3))
    h = histogram(dark)
    assert h["<4"] > 99.0, "an unmasked histogram must see near-black"


def test_compare_flags_a_sample_size_mismatch():
    full = _canvas()
    same = compare(full, full)
    assert same["sample_warning"] is False

    # A mask can shrink the sample dramatically; compare() has to say so.
    small = _canvas(20, 20)
    result = compare(full, small)
    assert result["reference_n"] == 240 * 180
    assert result["candidate_n"] == 20 * 20
    assert result["sample_warning"] is True

    # Just inside the 25% line: no warning.
    near = _canvas(240, 150)
    assert compare(full, near)["sample_warning"] is False


def test_command_line_refuses_to_measure_when_isolate_finds_no_object(tmp_path, capsys):
    """--isolate on a blank frame must stop, not quietly measure the background."""
    from pixeltruth.cli import main

    blank, lit = tmp_path / "blank.png", tmp_path / "lit.png"
    _canvas().save(blank)
    _with_object().save(lit)

    try:
        code = main(["compare", str(blank), str(lit), "--isolate"])
    except SystemExit as stop:
        code = stop.code
    out = capsys.readouterr()
    assert code not in (0, None)
    assert "no object found" in str(code)
    assert "reference_n" not in out.out and "field" not in out.out


def test_command_line_says_so_when_the_ground_is_too_uneven_to_outline_against(tmp_path, capsys):
    """A dark frame around a lit floor: the border says the ground is 10, so the
    outline would leak across the whole floor. It falls back to a brightness
    cutoff instead, and the user is told."""
    stage = _canvas(bg=(10, 10, 10))
    d = ImageDraw.Draw(stage)
    d.rectangle([8, 8, 231, 171], fill=(22, 22, 22))       # the lit floor
    d.rectangle([90, 60, 150, 120], fill=(220, 220, 220))  # the object
    path = tmp_path / "stage.png"
    stage.save(path)

    code, _, err = _run_cli(["compare", str(path), str(path), "--isolate"], capsys)
    assert code == 0
    assert "too uneven" in err

    # An even ground beside it: outlined against the ground, nothing printed.
    lit = tmp_path / "lit.png"
    _with_object().save(lit)
    code, _, err = _run_cli(["compare", str(lit), str(lit), "--isolate"], capsys)
    assert code == 0
    assert "too uneven" not in err


def test_command_line_refuses_to_measure_an_object_too_small_to_inset(tmp_path, capsys):
    """A thin bar is found but its core is under 20 px tall. Measuring the
    whole frame instead would mostly measure the background, so it must stop."""
    # Same frame size as the lit image, which compare now requires before it isolates.
    bar = _canvas()
    ImageDraw.Draw(bar).rectangle([20, 80, 220, 95], fill=(200, 200, 200))
    thin, lit = tmp_path / "thin.png", tmp_path / "lit.png"
    bar.save(thin)
    _with_object().save(lit)

    code, out, _ = _run_cli(["compare", str(lit), str(thin), "--isolate"], capsys)
    assert code not in (0, None)
    assert "too small" in str(code)
    assert "reference_n" not in out and "field" not in out


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


# ── failure mode 3, again: the command line must not resample to compare ─────

def _noisy_render(n, seed=3, sigma=6):
    """A smooth shaded square with independent per-pixel noise, `n` px on a side.

    Two of these at different `n` are the same renderer at two resolutions: the
    same quality, but grain (a neighbour statistic) is not comparable between them.
    """
    import random

    rng = random.Random(seed)
    im = Image.new("RGB", (n, n))
    data = []
    for y in range(n):
        for x in range(n):
            base = 60 + 140 * max(0.0, 1 - (((x - 0.4 * n) / n) ** 2 + ((y - 0.4 * n) / n) ** 2) * 2.2)
            v = max(0, min(255, int(round(base + rng.gauss(0, sigma)))))
            data.append((v, v, v))
    im.putdata(data)
    return im


def test_command_line_refuses_to_compare_grain_across_different_sizes(tmp_path, capsys):
    """Downscaling before measuring averages the noise away: the same renderer read
    grain 6.00 at one size and 2.00 at another once both were squeezed to the
    smaller. Different sizes must stop, not produce a number."""
    small, big = tmp_path / "small.png", tmp_path / "big.png"
    _noisy_render(100).save(small)
    _noisy_render(200).save(big)

    code, out, err = _run_cli(["compare", str(small), str(big)], capsys)
    assert code not in (0, None)
    assert "different sizes" in str(code)
    assert "--allow-different-sizes" in str(code)
    assert "field" not in out and "reference_n" not in out


def test_the_opt_in_flag_measures_each_image_at_native_resolution_and_warns(tmp_path, capsys):
    small, big = tmp_path / "small.png", tmp_path / "big.png"
    a, b = _noisy_render(100), _noisy_render(200)
    a.save(small)
    b.save(big)

    code, out, err = _run_cli(["compare", str(small), str(big), "--allow-different-sizes", "--json"], capsys)
    assert code == 0
    assert "not comparable" in err
    fields = json.loads(out)["fields"]
    # Native means native: the numbers are exactly what measure() gives for each file.
    assert fields["grain"]["reference"] == round(measure(a).grain, 2)
    assert fields["grain"]["candidate"] == round(measure(b).grain, 2)
    # And the noise really is the same at both sizes (it is per pixel), where the old
    # downscale-both path made the larger read about half.
    assert abs(fields["grain"]["reference"] - fields["grain"]["candidate"]) < 0.5, fields["grain"]


def test_same_size_images_compare_without_a_flag_or_a_warning(tmp_path, capsys):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _noisy_render(60, seed=1).save(a)
    _noisy_render(60, seed=2).save(b)
    code, out, err = _run_cli(["compare", str(a), str(b), "--json"], capsys)
    assert code == 0
    assert "not comparable" not in err
    assert json.loads(out)["reference_n"] == 60 * 60


def test_command_line_refuses_different_aspect_ratios_even_with_the_flag(tmp_path, capsys):
    """1000x500 and 800x600 used to be squashed to the same 800x500 rectangle, which
    bends one of the two images. Refuse, whatever flags are given."""
    wide, boxy = tmp_path / "wide.png", tmp_path / "boxy.png"
    Image.new("RGB", (400, 200), (90, 90, 90)).save(wide)
    Image.new("RGB", (320, 240), (90, 90, 90)).save(boxy)

    for extra in ([], ["--allow-different-sizes"]):
        code, out, _ = _run_cli(["compare", str(wide), str(boxy)] + extra, capsys)
        assert code not in (0, None), extra
        assert "aspect ratio" in str(code)
        assert "field" not in out and "reference_n" not in out


def test_specks_command_refuses_different_sizes_too(tmp_path, capsys):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _canvas(240, 180).save(a)
    _canvas(120, 90).save(b)
    code, out, _ = _run_cli(["specks", str(a), str(b)], capsys)
    assert code not in (0, None)
    assert "different sizes" in str(code)


# ── grain looks in both directions ───────────────────────────────────────────

def _stripes(horizontal, size=200, high=200):
    """Alternate lines of 0 and `high`. Horizontal stripes: rows differ, columns do not."""
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            on = (y if horizontal else x) % 2 == 0
            v = high if on else 0
            px[x, y] = (v, v, v)
    return im


def test_grain_sees_horizontal_stripes():
    """Grain used to look along rows only, so alternate rows of 0 and 200 read 0.00."""
    assert measure(_stripes(horizontal=True)).grain > 50.0


def test_grain_does_not_depend_on_which_way_the_texture_runs():
    h, v = measure(_stripes(horizontal=True)), measure(_stripes(horizontal=False))
    assert h.grain == v.grain
    assert measure(_stripes(True).transpose(Image.ROTATE_90)).grain == h.grain


def test_masked_grain_only_pairs_pixels_that_are_both_in_the_mask():
    """A pixel pair straddling the mask edge is not a pair inside the object."""
    im = _stripes(horizontal=True, size=40)
    inside = {(x, y) for x in range(40) for y in range(40) if y < 20}
    full, cut = measure(im), measure(im, mask=inside)
    assert cut.grain == full.grain          # stripes everywhere: the same 200 either way
    # One row has no vertical pairs at all, so only the (all equal) horizontal ones count.
    one_row = {(x, 5) for x in range(40)}
    assert measure(im, mask=one_row).grain == 0.0
    # Two touching rows add the vertical pairs: 0 along rows, 200 down columns.
    two_rows = {(x, y) for x in range(40) for y in (5, 6)}
    assert measure(im, mask=two_rows).grain == 100.0
    # Rows 5 and 7 do not touch, so there is still no vertical pair between them.
    apart = {(x, y) for x in range(40) for y in (5, 7)}
    assert measure(im, mask=apart).grain == 0.0
