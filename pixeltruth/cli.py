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

from .core import _outline, compare, histogram, core_box, speck_density


def _load(path: str) -> Image.Image:
    try:
        return Image.open(path).convert("RGB")
    except Exception as exc:
        raise SystemExit(f"cannot open {path}: {exc}")


def _prepare(img: Image.Image, use_isolate: bool, label: str):
    """Optionally reduce an image to the core of its main object."""
    if not use_isolate:
        return img
    mask, from_ground = _outline(img)
    if mask is None:
        raise SystemExit(f"{label}: no object found, so nothing was measured "
                         "(measuring the whole frame would measure the background)")
    if not from_ground:
        print(f"  ! {label}: the ground is too uneven to outline the object against, so it was\n"
              "    outlined with a brightness cutoff. A brighter or darker copy would be measured\n"
              "    over a different region: if brightness differs, crop both sides by hand.",
              file=sys.stderr)
    box = core_box(mask)
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw < 20 or bh < 20:
        raise SystemExit(f"{label}: object found, but its core ({bw}x{bh} px) is too small to "
                         "measure, so nothing was measured (measuring the whole frame would "
                         "mostly measure the background)")
    return img.crop(box)


ASPECT_TOLERANCE = 0.01   # relative difference in width/height still treated as one shape


def _require_comparable(a: Image.Image, b: Image.Image, allow_different_sizes: bool) -> None:
    """Stop unless the two images can be compared without resampling either.

    The old behaviour squeezed both images to the smaller one's size. Grain is a
    neighbour statistic, so that averaged the noise away on the larger side (the
    same renderer read 6.00 at 400 px and 2.00 at 800 px) and, when the shapes
    differed, bent one image to fit. That is failure mode 3, so nothing is
    resampled any more: each image is measured at its own native resolution.
    """
    if a.size == b.size:
        return
    ratio_a, ratio_b = a.width / a.height, b.width / b.height
    if abs(ratio_a - ratio_b) > ASPECT_TOLERANCE * max(ratio_a, ratio_b):
        raise SystemExit(
            f"the images have different aspect ratios ({a.width}x{a.height} and "
            f"{b.width}x{b.height}), so nothing was measured. Making them the same "
            "size would stretch one of them. Crop both to the same shape by the same rule, "
            "at their native resolution.")
    if not allow_different_sizes:
        raise SystemExit(
            f"the images have different sizes ({a.width}x{a.height} and {b.width}x{b.height}), "
            "so nothing was measured. Grain and the form/grain ratio are neighbour "
            "statistics and are not comparable between sizes, and resizing first would "
            "change them. Render or crop both to one size. To measure each at its own "
            "native resolution anyway, pass --allow-different-sizes.")
    print(f"  ! the images differ in size ({a.width}x{a.height} and {b.width}x{b.height}). "
          "Each was measured at its native resolution,\n"
          "    so grain and ratio are not comparable between the two sides; read only "
          "mean, percentiles,\n"
          "    saturation and dark_share.", file=sys.stderr)


def cmd_compare(args) -> int:
    ref = _load(args.reference)
    cand = _load(args.candidate)
    # Checked on the files as loaded, before --isolate crops them: the crops differ in
    # size whenever the objects do, which says nothing about the pixel grids.
    _require_comparable(ref, cand, args.allow_different_sizes)
    ref = _prepare(ref, args.isolate, "reference")
    cand = _prepare(cand, args.isolate, "candidate")

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
    _require_comparable(ref, cand, args.allow_different_sizes)
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
    c.add_argument("--allow-different-sizes", action="store_true",
                   help="measure images of different sizes at native resolution anyway "
                        "(grain and ratio are then not comparable)")
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
    s.add_argument("--allow-different-sizes", action="store_true",
                   help="measure images of different sizes at native resolution anyway "
                        "(grain and ratio are then not comparable)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_specks)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
