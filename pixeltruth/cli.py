"""Command line entry point.

    pixel-truth compare ref.png ours.png
    pixel-truth compare ref.png ours.png --isolate
    pixel-truth histogram ours.png
    pixel-truth specks ref.png ours.png
"""

from __future__ import annotations

import argparse
import json
import sys

from PIL import Image

from .core import compare, histogram, isolate, core_box, measure, speck_density


def _load(path: str) -> Image.Image:
    try:
        return Image.open(path).convert("RGB")
    except Exception as exc:
        raise SystemExit(f"cannot open {path}: {exc}")


def _prepare(img: Image.Image, use_isolate: bool, label: str):
    """Optionally reduce an image to the core of its largest bright object."""
    if not use_isolate:
        return img
    mask = isolate(img)
    if mask is None:
        print(f"  ! {label}: no object found, measuring the whole frame", file=sys.stderr)
        return img
    box = core_box(mask)
    if box[2] - box[0] < 20 or box[3] - box[1] < 20:
        print(f"  ! {label}: object too small to erode, measuring whole frame", file=sys.stderr)
        return img
    return img.crop(box)


def _same_scale(a: Image.Image, b: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Bring two images to one scale by DOWNSCALING the larger.

    Never upscale: an upscaled image's neighbouring pixels are interpolations of
    one source pixel, so neighbour statistics measure the resampler. That is
    failure mode 3.
    """
    if a.size == b.size:
        return a, b
    tw = min(a.width, b.width)
    th = min(a.height, b.height)
    resize = lambda im: im if im.size == (tw, th) else im.resize((tw, th), Image.LANCZOS)
    print(f"  i scales differ; downscaling both to {tw}x{th}", file=sys.stderr)
    return resize(a), resize(b)


def cmd_compare(args) -> int:
    ref = _load(args.reference)
    cand = _load(args.candidate)
    ref = _prepare(ref, args.isolate, "reference")
    cand = _prepare(cand, args.isolate, "candidate")
    ref, cand = _same_scale(ref, cand)

    result = compare(ref, cand, step=args.step, dark_below=args.dark_below)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"  reference n={result['reference_n']}   candidate n={result['candidate_n']}")
    if result["sample_warning"]:
        print("  ! SAMPLE SIZES DIFFER BY MORE THAN 25%.")
        print("    The two sides are not the same population; treat every row below")
        print("    as suspect. See docs/FAILURE-MODES.md #2.")
    print()
    print(f"  {'field':<12} {'reference':>10} {'candidate':>10} {'delta':>9} {'x':>7}")
    print("  " + "-" * 52)
    for name, row in result["fields"].items():
        x = f"{row['x']:.2f}" if row["x"] is not None else "-"
        print(
            f"  {name:<12} {row['reference']:>10.2f} {row['candidate']:>10.2f} "
            f"{row['delta']:>+9.2f} {x:>7}"
        )
    print()
    print("  ratio is form/grain: a lit solid is well above 1, noise on a shape is below.")
    return 0


def cmd_histogram(args) -> int:
    img = _load(args.image)
    out = histogram(img)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print("  share of EVERY pixel, no mask, no threshold:")
    for band, pct in out.items():
        bar = "#" * int(pct / 2)
        print(f"  {band:>8}  {pct:5.2f}%  {bar}")
    return 0


def cmd_specks(args) -> int:
    ref = _load(args.reference)
    cand = _load(args.candidate)
    ref, cand = _same_scale(ref, cand)
    a = speck_density(ref, above=args.above)
    b = speck_density(cand, above=args.above)
    if args.json:
        print(json.dumps({"reference": a, "candidate": b}, indent=2))
        return 0
    print(f"  {'':<12} {'left':>9} {'c-left':>9} {'c-right':>9} {'right':>9} {'total':>9} {'spread':>8}")
    for label, r in (("reference", a), ("candidate", b)):
        cols = "".join(f"{v:>10d}" for v in r["columns"])
        print(f"  {label:<12}{cols} {r['total_per_mpx']:>9d} {r['spread']:>8.2f}x")
    print()
    print("  per megapixel, local-median detector. spread is max/min across columns:")
    print("  a field that is even has a low spread; one stuck to one side does not.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pixel-truth",
        description="Measure a render against a reference without lying to yourself.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compare", help="run one instrument over both images")
    c.add_argument("reference")
    c.add_argument("candidate")
    c.add_argument("--isolate", action="store_true",
                   help="reduce each side to the core of its largest bright object")
    c.add_argument("--step", type=int, default=1, help="sample every Nth pixel (same on both sides)")
    c.add_argument("--dark-below", type=float, default=30.0)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_compare)

    h = sub.add_parser("histogram", help="unmasked luma histogram of every pixel")
    h.add_argument("image")
    h.add_argument("--json", action="store_true")
    h.set_defaults(func=cmd_histogram)

    s = sub.add_parser("specks", help="small-feature density in vertical columns")
    s.add_argument("reference")
    s.add_argument("candidate")
    s.add_argument("--above", type=float, default=12.0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_specks)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
