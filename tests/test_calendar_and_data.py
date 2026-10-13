"""Calendar features, the generator, and the feed cleaning."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from peak_capacity.calendar import day_features, regime
from peak_capacity.config import RAMADAN, mega_sale_day
from peak_capacity.generate import generate
from peak_capacity.transforms import calendar_table, clean


def test_ramadan_features_follow_the_lunar_calendar_not_the_gregorian_one() -> None:
    # The 25th night of Ramadan falls about eleven days earlier each year; a same-date-last-year
    # forecast reads it from the wrong part of the month.
    d25 = {y: RAMADAN[y][0] + timedelta(days=24) for y in (2024, 2025)}
    assert all(day_features(d)["ramadan_day"] == 25 for d in d25.values())
    year_ago = d25[2025] - timedelta(days=364)
    then = day_features(year_ago)
    assert then["ramadan_day"] == 16  # nine nights off, and outside the busiest last ten
    assert then["ramadan_last10"] == 0


def test_mega_sale_is_last_friday_of_november() -> None:
    for y in (2023, 2024, 2025):
        d = mega_sale_day(y)
        assert d.weekday() == 4 and d.month == 11 and (d + timedelta(days=7)).month == 12
        assert regime(day_features(d)) == "sale_day"


def test_generator_is_seeded_and_carries_the_surge() -> None:
    a = generate(seed=5, start=date(2025, 11, 1), end=date(2025, 11, 30))
    b = generate(seed=5, start=date(2025, 11, 1), end=date(2025, 11, 30))
    pd.testing.assert_frame_equal(a["hourly_orders"], b["hourly_orders"])
    h = a["hourly_orders"]
    daily = h.groupby("date")["orders"].sum()
    sale = daily[mega_sale_day(2025)]
    ordinary = daily[[date(2025, 11, 3), date(2025, 11, 4), date(2025, 11, 5)]].mean()
    assert 3.0 < sale / ordinary < 5.0
    assert h["orders_recorded"].isna().mean() > 0  # the feed has gaps


def test_cleaning_fills_gaps_and_replaces_a_planted_duplicate() -> None:
    h = generate(seed=1, start=date(2025, 4, 1), end=date(2025, 5, 31))["hourly_orders"]
    target = (h["hub"] == "A1") & (h["date"] == date(2025, 5, 14)) & (h["op_hour"] == 12)
    true_value = float(h.loc[target, "orders"].iloc[0])
    h.loc[target, "orders_recorded"] = 3 * true_value
    out = clean(h, calendar_table(date(2025, 4, 1), date(2025, 5, 31)))
    row = out[(out["hub"] == "A1") & (out["date"] == date(2025, 5, 14)) & (out["op_hour"] == 12)]
    assert bool(row["was_spike"].iloc[0])
    assert abs(float(row["orders_clean"].iloc[0]) - true_value) < 0.35 * true_value
    assert not out["orders_clean"].isna().any()
    assert np.isclose(out["was_spike"].mean(), 0, atol=0.01)
