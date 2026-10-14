"""Export the numbers drawn on slide 06 from the repo's own results.

- daily: replayed on-time share of accepted orders per peak day, all 12 hubs pooled, for the
  rule of thumb, the MIP at fluid capacity (derate 1.0) and the MIP at derate 0.85
  (results/hub_day.csv, 99% target, model forecast).
- disagree: the two derate-0.85 plans that differ only in how many slots the storefront sells
  (results/plan_summary.csv): on-time of accepted orders, on-time of all customers, turned away,
  lost margin.

Run:  cd <repo> && python3 <this file> <out.json>
"""

import json
import sys

import pandas as pd

PLANS = {"rule": "rule_of_thumb", "fluid": "mip_t99_d100", "derate": "mip_t99_d85"}

hd = pd.read_csv("results/hub_day.csv")
g = hd[hd["config"].isin(PLANS.values())].groupby(["config", "date"])[["on_time", "accepted"]].sum()
g["otd"] = g["on_time"] / g["accepted"]
segs = hd.drop_duplicates("date").set_index("date")["segment"]
dates = sorted(hd["date"].unique())
daily = [
    {"date": d, "segment": segs[d]} | {k: round(float(g.at[(c, d), "otd"]), 4) for k, c in PLANS.items()}
    for d in dates
]

ps = pd.read_csv("results/plan_summary.csv").set_index("config")
overall = {k: round(float(ps.at[c, "replay_otd"]), 4) for k, c in PLANS.items()}
disagree = [
    {
        "plan": c,
        "replay_otd": round(float(ps.at[c, "replay_otd"]), 4),
        "served_on_time_share": round(float(ps.at[c, "served_on_time_share"]), 4),
        "lost_share": round(float(ps.at[c, "lost_share"]), 4),
        "lost_margin": float(ps.at[c, "lost_margin"]),
        "total_cost": float(ps.at[c, "total_cost"]),
    }
    for c in ("mip_t99_d85", "mip_t99_d85_derated_caps")
]
out = {"daily": daily, "overall": overall, "disagree": disagree}
with open(sys.argv[1], "w") as fh:
    json.dump(out, fh, indent=1)
print(len(daily), "days; overall", overall)
