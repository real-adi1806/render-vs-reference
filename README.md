# pixel-truth

**Measure a render against a reference image without lying to yourself.**

Pixel-diff tools tell you *that* two images differ. This tells you *how*, in
numbers you can act on — and, more importantly, it is built to stop the four
specific ways those numbers quietly come out wrong.

```bash
pip install pillow
python -m pixeltruth.cli compare reference.png ours.png --isolate
```

```
  i scales differ; downscaling both to 284x248
  reference n=17608   candidate n=17608

  field         reference  candidate     delta       x
  ----------------------------------------------------
  mean             108.74      85.20    -23.54    0.78
  p10               75.59      39.03    -36.57    0.52
  p50               96.17      87.90     -8.28    0.91
  p90              174.75     123.47    -51.28    0.71
  saturation        20.19      25.58     +5.39    1.27
  grain              6.07       5.93     -0.14    0.98
  form              20.34      20.22     -0.12    0.99
  ratio              3.35       3.41     +0.06    1.02
  dark_share         0.00       6.57     +6.57       -
```

Read that: our surface texture is right (`grain`, `form`, `ratio` all within
2%), but it is too dark at the bottom end and carries 6.6% near-black that the
reference does not have. That is a specific, fixable statement. "12% of pixels
differ" is not.

---

## Why this exists

It was extracted from a real project: rebuilding an interactive site against a
reference, where every "match" had to be defended with a number.

Four times, the numbers were confidently wrong. Not noisy — **wrong in the
opposite direction**, with enough precision to be believed and acted on. One of
them produced a fix that made every metric worse.

Each one has the same shape: **you measured something other than the object.**

| # | The lie | What it cost |
|---|---|---|
| 1 | A fixed box on a moving object | Same element, same build, measured `86.4` and `157.9` |
| 2 | A threshold mask hides your own change | Built a fix; every number got worse; the sample had shrunk 12,554 → 11,034 |
| 3 | A magnified crop measures the upscaling | Grain read `1.07 vs 1.00` — a match. Native: `3.64 vs 2.50`, 46% off |
| 4 | Two scales wearing one name | `p90 105.0` quoted as CIE L\*, which caps at 100 |

**[docs/FAILURE-MODES.md](docs/FAILURE-MODES.md) is the real content of this
repo.** The code is the smaller half.

---

## What it measures

| | |
|---|---|
| `mean p10 p50 p90` | luma, **0-255**, named so you cannot confuse it with L\* |
| `dark_share` | fraction below a threshold you choose |
| `saturation` | median, 0-100 |
| `grain` | median absolute luma delta between adjacent pixels — *surface texture* |
| `form` | sd of 4×4 cell means — *variation across the object* |
| `ratio` | `form / grain` |

**`ratio` is the one worth knowing about.** A lit solid varies more *across
itself* than *between neighbouring pixels*, so its ratio is well above 1. Noise
painted on a silhouette is the exact inverse. A histogram cannot tell those
apart — two images with identical distributions can be a shaded object and
static — but this can, and it is a single number.

---

## Commands

```bash
# the main event: one instrument, both sides, same arguments
pixel-truth compare ref.png ours.png --isolate

# every pixel, no mask, no threshold. run this BEFORE concluding anything
# about tone, because a masked percentile can say the opposite
pixel-truth histogram ours.png

# small-feature density across four vertical columns
pixel-truth specks ref.png ours.png

# machine-readable for CI
pixel-truth compare ref.png ours.png --json
```

### `--isolate`

Finds the largest bright connected region, erodes to its core, and measures
inside that.

Use it whenever the thing you care about **moves, scales or travels**. A fixed
rectangle mixes object with background in a ratio that changes every frame, so
you are measuring an accidental blend. If no object is found it says so rather
than measuring the background.

---

## As a library

```python
from PIL import Image
from pixeltruth import isolate, core_box, measure, compare, histogram

ref  = Image.open("reference.png")
ours = Image.open("ours.png")

# isolate the subject on each side, then measure only that
mask = isolate(ours)
if mask:
    ours = ours.crop(core_box(mask))

print(measure(ours))
print(compare(ref, ours)["fields"]["p50"])
print(histogram(ours))          # unmasked, every pixel
```

`measure()` returns a `Stats` dataclass. **`Stats.n` is the sample size and it
is not decoration** — if `n` changes between runs of the same instrument, the
sample changed and your statistics are not comparable. `compare()` sets
`sample_warning` when the two sides differ by more than 25%.

---

## The rule

> **One instrument, both sides, same arguments, native resolution, sample size
> printed.**

Everything here enforces some part of that. `compare()` runs identical code over
both images. `_same_scale()` **downscales** the larger side and never upscales
the smaller. `measure()` always reports `n`. `luma()` says which scale it is in
its own docstring.

---

## Tests

The tests are written as the failure modes. Each constructs the situation that
produced a wrong answer and asserts the library now gives the right one —
including one test that asserts a **fixed box still gets it wrong**, so the
fixture cannot silently stop demonstrating the bug.

```bash
pip install pytest && pytest -q
```

A test that only checked "the function returns a number" would have passed
throughout every incident described here.

---

## What this is not

- **Not a pixel-diff.** It will not tell you which pixels changed. Use
  `odiff` or `pixelmatch` for that.
- **Not perceptual.** No SSIM, no CIEDE2000. It reports simple statistics with
  honest provenance, because the failures above were never about the
  sophistication of the metric.
- **Not a judge.** Numbers falsify; they do not verify. Three matching
  statistics out of twenty means nothing, and two images can match on every
  statistic here and still look nothing alike. **Look at the picture.**

That last point is the one I would keep if I could keep only one.

---

MIT. Contributions welcome, particularly new failure modes — if a measurement
has lied to you in a way not documented here, that is the most valuable thing
you could add.
