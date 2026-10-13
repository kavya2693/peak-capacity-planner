"""Write the seeded order history into data/. Usage: python scripts/generate_data.py [--seed N]"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from peak_capacity.generate import write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=int(os.environ.get("PEAK_SEED", "11")))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    tables = write(args.out, args.seed)
    h = tables["hourly_orders"]
    print(
        f"wrote {len(h):,} hub-hours, {h['orders'].sum():,} orders, "
        f"{h['hub'].nunique()} hubs, {h['date'].nunique()} days into {args.out} (seed {args.seed})"
    )
    print(tables["cities"].to_string(index=False))


if __name__ == "__main__":
    main()
