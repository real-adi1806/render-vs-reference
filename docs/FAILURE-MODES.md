# The four ways a pixel measurement lies to you

Every one of these was observed in production, on real work, by someone who
believed the number at the time. None of them are hypothetical, and none of
them announce themselves: each produces a **confident, precise, wrong** answer,
which is considerably worse than no answer.

They share one shape. **You measured something other than the object.**

---

## 1. A fixed box on a moving object

**The symptom.** The same element, on the same build, measured twice:

```
mean 86.4   "essentially an exact match"
mean 157.9  "1.84x too bright"
```

Both numbers were real. They were taken with two different crop rectangles.

**Why it happens.** Anything that moves, scales, or travels changes how much of
your fixed rectangle it fills. The rest of the rectangle is background, and the
statistic is a blend of the two in a ratio that changes every frame. You are
not measuring the object; you are measuring an accidental mixture.

**What it costs.** A whole investigation. The conclusion drawn from those two
numbers was "our elements are wildly inconsistent" and a fix was designed for
it. Measured properly, the spread was *1.60x against the reference's 1.79x*:
the inconsistency did not exist. The real defect was a uniform 40% brightness
error that the bad measurement had hidden behind fake variance.

**The fix.** `isolate()`. Find the object as a connected component, take the middle
of its bounding box (a 25% inset), measure inside that. If the object is not
found, `isolate()` returns None and the command line stops and measures nothing,
rather than measuring whatever was in the box.

**The fix had failure mode 2 in it.** The first `isolate()` drew the object's
outline with one brightness cutoff. A uniformly darker copy of the same object
kept fewer of its own pixels above that line, so its box shrank toward the
highlight and a different, brighter part of it was measured. On a test sphere
at 0.6x brightness it read `mean 0.64x` and `form 0.49x`, and the low `form`
was read as a lighting difference that did not exist. The outline now comes
from where the object differs from the ground, which does not move with the
object's brightness; the same pair reads `0.59x` and `0.61x`.

---

## 2. A threshold mask hides the change you just made

**The symptom.** A masked statistic said:

```
reference  41.6% of the object below L30
ours        6.4%
```

Read as: *their recesses are dark, ours are lit flat.* An occlusion pass was
built to deepen ours. **Every number then got worse.**

**Why it happens.** The mask was "pixels brighter than 14". Darkening pushed
pixels *below* that line, out of the sample entirely. The survivors were the
brighter ones, so the average rose. The instrument was excluding exactly the
pixels the change created.

**The tell was in the output the whole time**, and it was ignored:

```
n = 12,554  ->  n = 11,034
```

**What it actually was.** An unmasked full histogram, every pixel, no cutoff:

```
reference  0.0% below L10
ours      15.8% below L10
```

The reference had **no near-black anywhere**; its whole picture lived in a
narrow band. Ours had a hole in it. The defect was never the object: it was a
near-black band painted *behind* it, a trench cut out of the room. Three
separate attempts to fix the object were the wrong target.

**The fix.** Print `n` next to every statistic and treat a change in it as a red
flag. Run one unmasked `histogram()` before concluding anything about tone.
When a fix makes the metric worse, suspect the instrument before the fix.

---

## 3. A magnified crop measures the upscaling

**The symptom.** Surface grain, on a 4x magnified crop:

```
reference 1.00    ours 1.07    "a match"
```

At native resolution, the same two images:

```
reference 2.50    ours 3.64    46% off
```

**Why it happens.** Grain is a *neighbour* statistic. On a 4x crop, adjacent
pixels are interpolations of the same source pixel, so you are measuring the
resampler's smoothness, not the image's texture. Every upscaled comparison
converges toward "identical" no matter how different the sources are.

**The fix.** Measure at native resolution. Magnify to *look*, never to measure.
If the two sides are at different scales, do not resample either to match: the
command line stops. Downscaling the larger side averages its noise away (the same
renderer read grain 6.00 at 400 px and 2.00 at 800 px once squeezed to 400), and
upscaling the smaller measures the resampler. Render or crop both to one size.

---

## 4. Two scales wearing the same name

**The symptom.** A report quoted `p90 105.0` as a CIE L\* value.

**L\* caps at 100.** The number was Rec.709 luma on 0-255 and had been compared
against genuine L\* figures from the other side. The two sides differed by a
factor that was pure unit mismatch, and a fix was prescribed in the wrong
direction because of it.

**The fix.** Name the scale in the variable, the output and the docstring. This
library uses **luma, 0-255**, everywhere, and says so in `core.luma`. If you
need L\*, convert explicitly and rename.

---

## The rule underneath all four

> **One instrument, both sides, same arguments, native resolution, sample size
> printed.**

`compare()` exists to enforce that: it runs identical code over both images and
flags a sample-size mismatch, because a statistic from a different method on
each side is not a comparison at all.

---

## A fifth, for when you delegate

If you hand measurement to someone or something else, **test the premise before
you act on the prescription.**

A careful analysis once reported that an object's contact shading was
sign-inverted, and prescribed a multi-hour bisect through five rendering terms.
Measuring the source asset directly took ten minutes:

```
crease mean 43.8    ridge mean 174.6
creases are 130.8 DARKER, and 96% of that survives the downscale
```

The shading was correct at every stage. The analysis had measured frames in
which the object was *behind frosted glass*, and a crease holds more frost. It
had measured the glass. The prescribed fix would have found nothing, slowly.

Premises are cheap to test and prescriptions are expensive to follow.
