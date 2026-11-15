"""Generate the demo dataset.

A synthetic classification set keeps the assignment focused on the MLOps
workflow rather than on data cleaning. The variant flag exists so a second,
genuinely different dataset version can be produced and tracked with DVC, which
is what the provenance and comparison tools are demonstrated against.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "data" / "dataset.csv"

# Each variant is a different dataset version: more rows and a cleaner signal in
# v2, so the two versions produce visibly different model metrics.
VARIANTS = {
    "v1": {"n_samples": 1200, "noise": 0.35, "seed": 7},
    "v2": {"n_samples": 2000, "noise": 0.20, "seed": 11},
}


def generate(variant: str) -> pd.DataFrame:
    config = VARIANTS[variant]
    rng = np.random.default_rng(config["seed"])
    n = config["n_samples"]

    sensor_a = rng.normal(0.0, 1.0, n)
    sensor_b = rng.normal(0.5, 1.5, n)
    sensor_c = rng.gamma(2.0, 1.0, n)
    load = rng.uniform(0.0, 1.0, n)

    signal = 0.9 * sensor_a + 0.6 * sensor_b - 0.4 * sensor_c + 1.2 * load
    signal += rng.normal(0.0, config["noise"] * 3, n)
    label = (signal > np.median(signal)).astype(int)

    return pd.DataFrame(
        {
            "sensor_a": sensor_a.round(5),
            "sensor_b": sensor_b.round(5),
            "sensor_c": sensor_c.round(5),
            "load": load.round(5),
            "failure": label,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the demo dataset.")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    frame = generate(args.variant)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"wrote {len(frame)} rows of variant {args.variant} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
