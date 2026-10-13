"""Hourly demand forecasts for a peak window, made as of a cutoff date.

Two forecasters are compared:

* `seasonal_naive`: the same weekday 52 weeks earlier, scaled by the hub's year-on-year growth,
  with that day's own hourly shape. This is what a spreadsheet plan copies from last year.
* `event_model`: a gradient-boosted model of each day's demand relative to the hub's recent
  ordinary level, driven by features counted from the events themselves (day of Ramadan, days
  to Eid, days to the sale), then split into hours with the shape of that kind of day.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from peak_capacity.calendar import calendar_frame, regime
from peak_capacity.config import N_HOURS, Window
from peak_capacity.transforms import hourly_profiles

FEATURES = [
    "dow",
    "weekend",
    "summer",
    "ramadan_day",
    "ramadan_last10",
    "days_to_ramadan",
    "eid_fitr_offset",
    "eid_adha_offset",
    "sale_offset",
    "national_day",
]
LEVEL_WINDOW = 28  # days of ordinary demand averaged into a hub's level
LEVEL_LAG = 45  # training rows see the level as it stood this many days earlier,
# roughly the distance from a planning cutoff to the middle of the window being planned


def hub_level(daily: pd.DataFrame) -> pd.DataFrame:
    """Trailing mean of ordinary (non-event, non-closure) days per hub, by date."""
    out = []
    for hub, g in daily.sort_values("date").groupby("hub"):
        s = g.set_index("date")["orders"].astype(float)
        s[g.set_index("date")["event_day"] | g.set_index("date")["outage"]] = np.nan
        s.index = pd.to_datetime(s.index)
        s = s.asfreq("D")
        lvl = s.rolling(LEVEL_WINDOW, min_periods=10).mean()
        out.append(pd.DataFrame({"date": lvl.index.date, "hub": hub, "level": lvl.to_numpy()}))
    return pd.concat(out, ignore_index=True)


def _level_on(levels: pd.DataFrame, hub: str, d: date) -> float:
    return float(levels.set_index(["hub", "date"]).at[(hub, d), "level"])


def event_model(
    daily: pd.DataFrame, clean_hourly: pd.DataFrame, window: Window, seed: int = 0
) -> pd.DataFrame:
    """Hourly forecast (date, hub, city, op_hour, forecast) for every day in the window."""
    cal = calendar_frame(daily["date"].min(), window.end)
    levels = hub_level(daily)
    lagged = levels.assign(date=[d + timedelta(days=LEVEL_LAG) for d in levels["date"]])
    train = (
        daily[(daily["date"] < window.cutoff) & ~daily["outage"]]
        .merge(lagged.rename(columns={"level": "lvl"}), on=["hub", "date"])
        .merge(cal, on="date")
        .dropna(subset=["lvl"])
    )
    model = HistGradientBoostingRegressor(
        loss="poisson", max_iter=500, learning_rate=0.05, max_leaf_nodes=31, random_state=seed
    )
    # Learning the ratio to the hub's own level lets twelve hubs of different size share one model.
    model.fit(train[FEATURES], train["orders"] / train["lvl"], sample_weight=train["lvl"])

    days = calendar_frame(window.start, window.end)
    hubs = daily[["hub", "city"]].drop_duplicates()
    grid = hubs.merge(days, how="cross")
    as_of = {h: _level_on(levels, h, window.cutoff - timedelta(days=1)) for h in hubs["hub"]}
    grid["level"] = grid["hub"].map(as_of)
    grid["daily_forecast"] = model.predict(grid[FEATURES]) * grid["level"]
    grid["regime"] = [regime(r) for _, r in grid.iterrows()]

    prof = hourly_profiles(clean_hourly, window.cutoff)
    out = grid[["date", "hub", "city", "regime", "daily_forecast"]].merge(prof, on=["city", "regime"])
    out["forecast"] = out["daily_forecast"] * out["share"]
    return out[["date", "hub", "city", "op_hour", "forecast"]].sort_values(["hub", "date", "op_hour"])


def seasonal_naive(clean_hourly: pd.DataFrame, daily: pd.DataFrame, window: Window) -> pd.DataFrame:
    levels = hub_level(daily)
    rows = []
    n_days = (window.end - window.start).days + 1
    window_days = {window.start + timedelta(days=i - 364) for i in range(n_days)}
    last_year = {
        key: g.sort_values("op_hour")["orders_clean"].to_numpy()
        for key, g in clean_hourly[clean_hourly["date"].isin(window_days)].groupby(["hub", "date"])
    }
    for hub, city in daily[["hub", "city"]].drop_duplicates().itertuples(index=False):
        now = _level_on(levels, hub, window.cutoff - timedelta(days=1))
        then = _level_on(levels, hub, window.cutoff - timedelta(days=365))
        growth = now / then
        for i in range(n_days):
            d = window.start + timedelta(days=i)
            vals = last_year[(hub, d - timedelta(days=364))] * growth
            rows.append(
                pd.DataFrame(
                    {"date": d, "hub": hub, "city": city, "op_hour": np.arange(N_HOURS), "forecast": vals}
                )
            )
    return pd.concat(rows, ignore_index=True)


def accuracy(fc: pd.DataFrame, actual: pd.DataFrame, segment: pd.Series | None = None) -> pd.DataFrame:
    """WAPE and bias of a forecast against true demand, at daily and hourly grain.

    `segment` maps date -> label so the easy and hard parts of a window are reported apart.
    """
    m = fc.merge(actual[["date", "hub", "op_hour", "orders"]], on=["date", "hub", "op_hour"])
    m["segment"] = m["date"].map(segment) if segment is not None else "all"
    out = []
    for seg, g in [*m.groupby("segment"), ("all", m)]:
        d = g.groupby(["date", "hub"])[["forecast", "orders"]].sum()
        out.append(
            {
                "segment": seg,
                "hub_days": len(d),
                "daily_wape": float((d["forecast"] - d["orders"]).abs().sum() / d["orders"].sum()),
                "hourly_wape": float((g["forecast"] - g["orders"]).abs().sum() / g["orders"].sum()),
                "bias": float(g["forecast"].sum() / g["orders"].sum() - 1),
            }
        )
    return pd.DataFrame(out)
