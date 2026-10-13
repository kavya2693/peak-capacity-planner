"""One command: clean the feed, forecast both peak windows, plan every configuration, replay it.

Usage: peak-capacity [--quick] [--out results] [--data data] [--workers N]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd

from peak_capacity import transforms
from peak_capacity.experiment import default_configs, run
from peak_capacity.report import write_report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--quick", action="store_true", help="three peak days per window, three configs")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)

    tables = transforms.load(args.data)
    cal = transforms.calendar_table()
    clean = transforms.clean(tables["hourly"], cal)
    daily = transforms.daily_orders(clean)
    report = transforms.cleaning_report(clean)
    log(f"cleaned feed: {report.to_dict('records')[0]}")

    configs = default_configs(args.quick)
    out = run(
        tables["hourly"],
        clean,
        daily,
        tables["hubs"],
        tables["cities"],
        configs,
        quick=args.quick,
        workers=args.workers,
        seed=args.seed,
        log=log,
    )
    out["cleaning"] = report

    args.out.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        df.to_csv(args.out / f"{name}.csv", index=False, float_format="%.6g")
    write_report(out, args.out / "run_report.md")
    summary = out["plan_summary"].set_index("config")
    (args.out / "summary.json").write_text(
        json.dumps(json.loads(summary.to_json(orient="index")), indent=2) + "\n"
    )
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(
            summary[["rider_cost", "lost_margin", "total_cost", "planned_otd", "replay_otd", "lost_share"]]
            .round(4)
            .to_string()
        )
    log(f"done; tables in {args.out}/")


if __name__ == "__main__":
    main()
