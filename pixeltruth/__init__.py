"""pixel-truth: measure a render against a reference without lying to yourself."""

from .core import (  # noqa: F401
    Stats,
    compare,
    core_box,
    histogram,
    isolate,
    luma,
    measure,
    speck_density,
)

__version__ = "0.1.0"
__all__ = [
    "Stats", "compare", "core_box", "histogram",
    "isolate", "luma", "measure", "speck_density",
]
