"""Seeded order history for 12 hubs in three cities, January 2023 to December 2025.

Writes the hourly order counts the forecaster learns from and the replay is scored against,
plus the hub and city tables the planner needs. Two columns are written per hub-hour:
`orders` is what customers asked for, `orders_recorded` is what the order feed captured,
with the feed gaps and the duplicated-event spike a real feed has.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from peak_capacity.calendar import calendar_frame, regime
from peak_capacity.config import (
    CITIES,
    DATA_END,
    DATA_START,
    HUBS,
    N_HOURS,
    OPEN_CLOCK_HOUR,
    OWN_AVAILABILITY,
    OWN_SHIFT_HOURS,
    SPACE_MULTIPLE,
)

# Relative order intensity per operating hour (07:00 first, 02:00 last).
PROFILES: dict[str, list[float]] = {
    "weekday": [0.8, 1.6, 2.6, 3.4, 4.4, 5.6, 5.8, 4.8, 4.4, 5.0,
                6.2, 7.4, 8.2, 8.4, 7.6, 6.0, 4.2, 2.4, 1.2, 0.5],
    "weekend": [0.6, 1.4, 3.0, 4.6, 5.6, 6.2, 6.2, 5.8, 5.4, 5.6,
                6.2, 7.0, 7.6, 7.6, 6.8, 5.4, 4.0, 2.4, 1.2, 0.5],
    # Groceries before iftar, a collapse while people break the fast, then a late-night surge.
    "ramadan": [0.6, 0.8, 1.2, 1.8, 2.6, 3.4, 4.0, 4.8, 6.0, 8.4,
                10.0, 5.0, 2.4, 5.2, 8.0, 9.6, 9.8, 8.6, 6.0, 3.4],
    "eid": [0.3, 0.5, 1.0, 2.0, 3.6, 5.0, 6.0, 6.4, 6.4, 6.4,
            6.6, 6.8, 7.0, 7.0, 6.6, 5.6, 4.4, 3.0, 1.6, 0.7],
    # The sale opens at midnight while hubs are shut, so the backlog of browsing lands at opening.
    "sale_day": [3.0, 5.0, 6.4, 7.0, 7.4, 8.0, 8.2, 7.6, 7.2, 7.4,
                 8.0, 9.0, 10.6, 12.0, 11.2, 9.4, 7.6, 5.2, 3.0, 1.4],
}  # fmt: skip

DOW_FACTOR = (0.95, 0.95, 0.97, 1.0, 1.08, 1.12, 1.03)
ANNUAL_GROWTH = 0.16
# The mega-sale grows every year; last year's sale is not a ceiling for this year's.
SALE_INTENSITY = {2023: 3.3, 2024: 3.5, 2025: 3.8}
SALE_AFTER = {1: 2.2, 2: 1.5, 3: 1.6, 4: 1.1}


def profile(name: str) -> np.ndarray:
    p = np.array(PROFILES[name], dtype=float)
    return p / p.sum()


def day_multiplier(feats: pd.Series, year: int) -> float:
    m = DOW_FACTOR[int(feats["dow"])]
    if feats["summer"]:
        m *= 0.85
    if feats["national_day"]:
        m *= 1.15
    if 1 <= feats["days_to_ramadan"] <= 7:
        m *= 1.10
    rd = int(feats["ramadan_day"])
    if rd > 0:
        # Weekly rhythm flattens in Ramadan; demand climbs into the last ten nights.
        m = 1.0 + 0.4 * (m - 1.0)
        m *= 1.15 + 0.10 * min(rd, 19) / 19 + (0.15 + 0.01 * (rd - 19) if feats["ramadan_last10"] else 0)
    eo = int(feats["eid_fitr_offset"])
    if eo == -1:
        m *= 1.35
    elif eo in (0, 1, 2):
        m *= (0.7, 0.8, 0.9)[eo]
    ao = int(feats["eid_adha_offset"])
    if ao == -1:
        m *= 1.3
    elif ao in (0, 1, 2):
        m *= 0.75
    so = int(feats["sale_offset"])
    if -10 <= so <= -1:
        m *= 1.05 + 0.025 * (10 + so)
    elif so == 0:
        m *= SALE_INTENSITY.get(year, 3.8)
    elif so in SALE_AFTER:
        m *= SALE_AFTER[so]
    return float(m)


def generate(seed: int = 11, start: date = DATA_START, end: date = DATA_END) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    cal = calendar_frame(start, end)
    n_days = len(cal)
    years_from_2025 = np.array([(d - date(2025, 1, 1)).days / 365.25 for d in cal["date"]])
    growth = (1 + ANNUAL_GROWTH) ** years_from_2025
    regimes = [regime(row) for _, row in cal.iterrows()]
    mult = np.array([day_multiplier(row, row["date"].year) for _, row in cal.iterrows()])

    # Two rain days a year per city lift orders by a third.
    rain = {c: np.zeros(n_days, dtype=bool) for c in CITIES}
    for c in CITIES:
        for y in sorted({d.year for d in cal["date"]}):
            idx = np.flatnonzero([d.year == y and d.month in (1, 2, 12) for d in cal["date"]])
            rain[c][rng.choice(idx, size=2, replace=False)] = True

    frames = []
    for hub in HUBS:
        hub_noise = np.exp(rng.normal(0, 0.06, n_days))
        daily = hub.base_daily * growth * mult * hub_noise * np.where(rain[hub.city], 1.35, 1.0)
        means = np.stack([daily[i] * profile(regimes[i]) for i in range(n_days)])
        # Gamma-Poisson: hour counts are over-dispersed compared with Poisson.
        lam = rng.gamma(shape=30.0, scale=means / 30.0)
        orders = rng.poisson(lam).astype(float)
        if hub.hub == "B3":  # two-day closure for a refit; nobody could order
            closed = np.flatnonzero(
                (cal["date"] >= date(2024, 6, 3)).to_numpy() & (cal["date"] <= date(2024, 6, 4)).to_numpy()
            )
            orders[closed, :] = 0
        recorded = orders.copy()
        gaps = rng.random(recorded.shape) < 0.005
        recorded[gaps] = np.nan
        if hub.hub == "A2":  # an order-event replay duplicated three hours of the feed
            i = int(np.flatnonzero((cal["date"] == date(2024, 9, 12)).to_numpy())[0])
            recorded[i, 3:6] *= 2
        frames.append(
            pd.DataFrame(
                {
                    "date": np.repeat(cal["date"].to_numpy(), N_HOURS),
                    "hub": hub.hub,
                    "city": hub.city,
                    "op_hour": np.tile(np.arange(N_HOURS), n_days),
                    "clock_hour": np.tile((np.arange(N_HOURS) + OPEN_CLOCK_HOUR) % 24, n_days),
                    "orders": orders.ravel().astype(int),
                    "orders_recorded": recorded.ravel(),
                }
            )
        )
    hourly = pd.concat(frames, ignore_index=True)

    # Sizing reflects how the network was staffed going into 2025: own riders for an ordinary
    # weekday plus a margin, and hub space for three times the ordinary peak.
    weekday = profile("weekday")
    hub_rows, city_need = [], dict.fromkeys(CITIES, 0.0)
    for hub in HUBS:
        peak_riders = hub.base_daily * weekday.max() * hub.trip_minutes / 60
        rider_hours = hub.base_daily * hub.trip_minutes / 60 / 0.75
        city_need[hub.city] += rider_hours / OWN_SHIFT_HOURS
        hub_rows.append(
            {
                "hub": hub.hub,
                "city": hub.city,
                "base_daily": hub.base_daily,
                "trip_minutes": hub.trip_minutes,
                "max_on_duty": int(np.ceil(SPACE_MULTIPLE * peak_riders)),
            }
        )
    cities = pd.DataFrame(
        [{"city": c, "own_pool": int(np.ceil(1.1 * city_need[c] / OWN_AVAILABILITY))} for c in CITIES]
    )
    return {"hourly_orders": hourly, "hubs": pd.DataFrame(hub_rows), "cities": cities}


def write(out_dir: Path, seed: int = 11) -> dict[str, pd.DataFrame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = generate(seed)
    tables["hourly_orders"].to_csv(out_dir / "hourly_orders.csv.gz", index=False)
    tables["hubs"].to_csv(out_dir / "hubs.csv", index=False)
    tables["cities"].to_csv(out_dir / "cities.csv", index=False)
    return tables
