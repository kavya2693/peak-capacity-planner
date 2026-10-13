"""Event calendar features known in advance of any forecast.

Ramadan moves about eleven days earlier every Gregorian year, so a feature built on the
Gregorian date (week of year, same day last year) lands on the wrong part of the month. Every
event feature here is counted from the event itself instead.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from peak_capacity.config import EID_ADHA, NATIONAL_DAY, RAMADAN, mega_sale_day

FAR = 99  # sentinel for "not near this event"


def _ramadan_for(d: date) -> tuple[date, date] | None:
    for start, eid in RAMADAN.values():
        if start - timedelta(days=10) <= d <= eid + timedelta(days=10):
            return start, eid
    return None


def day_features(d: date) -> dict[str, int]:
    """Calendar features for one date. All are integers so trees and SQL handle them alike."""
    feats: dict[str, int] = {
        "dow": d.weekday(),
        "weekend": int(d.weekday() >= 5),
        "month": d.month,
        "summer": int(d.month in (7, 8)),
        "ramadan_day": 0,
        "ramadan_last10": 0,
        "days_to_ramadan": FAR,
        "eid_fitr_offset": FAR,
        "eid_adha_offset": FAR,
        "sale_offset": FAR,
        "national_day": int((d.month, d.day) in (NATIONAL_DAY, (NATIONAL_DAY[0], NATIONAL_DAY[1] + 1))),
    }
    r = _ramadan_for(d)
    if r is not None:
        start, eid = r
        if start <= d < eid:
            feats["ramadan_day"] = (d - start).days + 1
            feats["ramadan_last10"] = int((eid - d).days <= 10)
        elif d < start:
            feats["days_to_ramadan"] = (start - d).days
        off = (d - eid).days
        if -3 <= off <= 4:
            feats["eid_fitr_offset"] = off
    adha = EID_ADHA.get(d.year)
    if adha is not None and -3 <= (d - adha).days <= 4:
        feats["eid_adha_offset"] = (d - adha).days
    off = (d - mega_sale_day(d.year)).days
    if -10 <= off <= 4:
        feats["sale_offset"] = off
    return feats


def calendar_frame(start: date, end: date) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    rows = [{"date": ts.date(), **day_features(ts.date())} for ts in days]
    return pd.DataFrame(rows)


def regime(feats: dict[str, int] | pd.Series) -> str:
    """Which hourly shape a day follows. Used for profiles and for the replay's traffic."""
    if feats["sale_offset"] == 0:
        return "sale_day"
    if 0 <= feats["eid_fitr_offset"] <= 2 or 0 <= feats["eid_adha_offset"] <= 2:
        return "eid"
    if feats["ramadan_day"] > 0:
        return "ramadan"
    return "weekend" if feats["weekend"] else "weekday"


def is_event_day(feats: dict[str, int] | pd.Series) -> bool:
    """Days excluded when estimating a hub's ordinary level."""
    return bool(
        feats["ramadan_day"] > 0
        or feats["eid_fitr_offset"] != FAR
        or feats["eid_adha_offset"] != FAR
        or feats["sale_offset"] != FAR
        or feats["national_day"]
        or feats["days_to_ramadan"] <= 3
    )


def regimes_for(dates: pd.Series) -> np.ndarray:
    return np.array([regime(day_features(d)) for d in dates])
