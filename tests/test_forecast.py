"""Forecasters on the full three-year history, scored on the Ramadan window."""

from __future__ import annotations

import pandas as pd

from peak_capacity.config import WINDOWS
from peak_capacity.experiment import segment
from peak_capacity.forecast import accuracy, event_model, seasonal_naive
from peak_capacity.generate import generate
from peak_capacity.transforms import calendar_table, clean, daily_orders


def test_event_model_beats_last_year_on_ramadan_and_never_peeks_past_cutoff() -> None:
    hourly = generate(seed=2)["hourly_orders"]
    w = WINDOWS[0]
    ch = clean(hourly, calendar_table())
    daily = daily_orders(ch)
    # Anything after the cutoff is destroyed: a forecaster that reads it would change.
    blinded = ch.copy()
    blinded.loc[blinded["date"] >= w.cutoff, "orders_clean"] = 0.0
    blinded_daily = daily_orders(blinded)
    model = event_model(blinded_daily, blinded, w)
    pd.testing.assert_frame_equal(
        model.reset_index(drop=True), event_model(daily, ch, w).reset_index(drop=True)
    )
    naive = seasonal_naive(blinded, blinded_daily, w)
    days = sorted(model["date"].unique())
    seg = pd.Series({d: segment(d) for d in days})
    m = accuracy(model, hourly, seg).set_index("segment")
    n = accuracy(naive, hourly, seg).set_index("segment")
    assert m.loc["ramadan_last10", "hourly_wape"] < n.loc["ramadan_last10", "hourly_wape"]
    assert m.loc["eid", "daily_wape"] < n.loc["eid", "daily_wape"]
