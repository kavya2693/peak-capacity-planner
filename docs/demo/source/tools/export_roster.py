"""Re-run the repo's own pipeline for ONE day and export the sale-day rider roster.

Same data, cleaning, forecaster (event model, as of 2025-10-31), planner settings
(MIP, 99% on-time target, derate 0.85, slots sold at the flat-out rate) and replay seeds as
`make run` (config `mip_t99_d85`), restricted to the mega-sale day 2025-11-28 for all three
cities. Per hub it writes the shifts, riders on duty and slots per hour, the forecast, and the
replay result. Each hub's totals can be compared with the matching row of results/hub_day.csv.

Run:  cd <repo> && .venv/bin/python <this file> <out.json>
"""

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

from peak_capacity import transforms
from peak_capacity.calendar import day_features, regime
from peak_capacity.config import (
    CITIES,
    GIG_BLOCK_HOURS,
    OPEN_CLOCK_HOUR,
    OWN_AVAILABILITY,
    OWN_SHIFT_HOURS,
    WINDOWS,
)
from peak_capacity.forecast import event_model
from peak_capacity.plan import plan_city_day
from peak_capacity.replay import replay_hub_day
from peak_capacity.service import fluid_rate, traffic

DAY = date(2025, 11, 28)
TARGET, DERATE, CAP_RULE, SEED = 0.99, 0.85, "fluid", 0

tables = transforms.load(Path("data"))
cal = transforms.calendar_table()
clean = transforms.clean(tables["hourly"], cal)
daily = transforms.daily_orders(clean)
hubs, cities = tables["hubs"], tables["cities"]
window = next(w for w in WINDOWS if w.start <= DAY <= w.end)
fc = event_model(daily, clean, window, SEED)

truth = {
    h: g.sort_values("op_hour")["orders"].to_numpy(dtype=float)
    for h, g in tables["hourly"][tables["hourly"]["date"] == DAY].groupby("hub")
}
fcd = {
    h: g.sort_values("op_hour")["forecast"].to_numpy(dtype=float)
    for h, g in fc[fc["date"] == DAY].groupby("hub")
}
trip = dict(zip(hubs["hub"], hubs["trip_minutes"], strict=True))
max_on = dict(zip(hubs["hub"], hubs["max_on_duty"], strict=True))
own_avail = {r.city: math.floor(r.own_pool * OWN_AVAILABILITY) for r in cities.itertuples()}
hub_index = {h: i for i, h in enumerate(hubs["hub"])}
reg = regime(day_features(DAY))


def clock(op_hour: int) -> str:
    return f"{(OPEN_CLOCK_HOUR + op_hour) % 24:02d}:00"


rows = []
for c in CITIES:
    hs = hubs.loc[hubs["city"] == c, "hub"].tolist()
    plan = plan_city_day(
        c,
        {h: fcd[h] for h in hs},
        {h: fluid_rate(trip[h], reg) for h in hs},
        {h: max_on[h] for h in hs},
        own_avail[c],
        TARGET,
        DERATE,
        CAP_RULE,
    )
    for hp in plan.hubs:
        res = replay_hub_day(
            truth[hp.hub],
            hp.slot_cap,
            hp.own,
            hp.gig,
            trip[hp.hub],
            traffic(reg),
            seed=(SEED, hub_index[hp.hub], DAY.toordinal()),
        )
        busiest = int(np.argmax(fcd[hp.hub]))
        rows.append(
            {
                "hub": hp.hub,
                "city": c,
                "solver_status": plan.status,
                "own_shifts": {clock(s): n for s, n in sorted(hp.own.items())},
                "gig_blocks": {clock(s): n for s, n in sorted(hp.gig.items())},
                "own_riders": int(sum(hp.own.values())),
                "gig_total": int(sum(hp.gig.values())),
                "own_shift_hours": OWN_SHIFT_HOURS,
                "gig_block_hours": GIG_BLOCK_HOURS,
                "riders_on_duty": [int(x) for x in hp.riders],
                "peak_riders": int(hp.riders.max()),
                "max_on_duty": int(max_on[hp.hub]),
                "slot_cap": [round(float(x), 1) for x in hp.slot_cap],
                "forecast": [round(float(x), 1) for x in fcd[hp.hub]],
                "forecast_orders": round(float(fcd[hp.hub].sum()), 1),
                "busiest_hour": clock(busiest),
                "busiest_forecast": round(float(fcd[hp.hub][busiest]), 1),
                "busiest_slots": math.floor(hp.slot_cap[busiest] + 1e-9),
                "riders_at_busiest": int(hp.riders[busiest]),
                "rider_cost": float(hp.rider_cost),
                "demand": res.demand,
                "accepted": res.accepted,
                "lost": res.lost,
                "on_time": res.on_time,
                "late": res.late,
                "undelivered": res.undelivered,
                "otd": round(res.otd, 4),
            }
        )

tot = {
    k: sum(r[k] for r in rows)
    for k in ("own_riders", "gig_total", "rider_cost", "demand", "accepted", "lost", "on_time")
}
out = {
    "day": str(DAY),
    "config": "mip_t99_d85",
    "hours": [clock(t) for t in range(len(rows[0]["forecast"]))],
    "totals": tot
    | {
        "otd": round(tot["on_time"] / tot["accepted"], 4),
        "lost_share": round(tot["lost"] / tot["demand"], 4),
    },
    "hubs": rows,
}
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(out["totals"])
for r in rows:
    print(
        r["hub"],
        r["solver_status"],
        r["own_riders"],
        r["gig_total"],
        r["peak_riders"],
        r["on_time"],
        r["otd"],
    )
