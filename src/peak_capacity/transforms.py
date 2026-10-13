"""SQL cleaning of the order feed and the aggregates the forecaster and planner read.

The feed has gaps, one duplicated-event spike and a hub closure. Cleaning is done in DuckDB
because each rule is a window over the same hub, hour and kind of day, which reads more
plainly as SQL than as chained pandas groupbys.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from peak_capacity.calendar import calendar_frame, is_event_day, regime
from peak_capacity.config import DATA_END, DATA_START

SPIKE_SIGMAS = 4.0  # how far above its expected count a recorded hour must be to count as an error
OUTAGE_RATIO = 0.3  # a day this far below the hub's recent median is a closure, not demand


def load(data_dir: Path) -> dict[str, pd.DataFrame]:
    hourly = pd.read_csv(data_dir / "hourly_orders.csv.gz", parse_dates=["date"])
    hourly["date"] = hourly["date"].dt.date
    return {
        "hourly": hourly,
        "hubs": pd.read_csv(data_dir / "hubs.csv"),
        "cities": pd.read_csv(data_dir / "cities.csv"),
    }


def calendar_table(start: date = DATA_START, end: date = DATA_END) -> pd.DataFrame:
    cal = calendar_frame(start, end)
    cal["regime"] = [regime(r) for _, r in cal.iterrows()]
    cal["event_day"] = [is_event_day(r) for _, r in cal.iterrows()]
    return cal


def clean(hourly: pd.DataFrame, cal: pd.DataFrame) -> pd.DataFrame:
    """Hourly recorded orders with gaps filled and spikes replaced; adds daily outage flags."""
    con = duckdb.connect()
    con.register("hourly_raw", hourly)
    con.register("cal", cal[["date", "regime", "event_day"]])
    out = con.execute(
        f"""
        WITH h AS (
            SELECT r.date, r.hub, r.city, r.op_hour, r.orders_recorded AS rec, c.regime, c.event_day
            FROM hourly_raw r JOIN cal c USING (date)
        ),
        shares AS (
            SELECT *,
                rec / nullif(sum(rec) OVER (PARTITION BY hub, date), 0) AS share,
                sum(rec) OVER (PARTITION BY hub, date) AS day_rec
            FROM h
        ),
        ref AS (
            -- Comparing an hour's share of its own day, not its raw count, cancels the day-level
            -- swings (weekends, sale week, rain) that would otherwise look like spikes.
            SELECT *,
                median(share) OVER (
                    PARTITION BY hub, op_hour, regime ORDER BY date
                    RANGE BETWEEN INTERVAL 14 DAYS PRECEDING AND INTERVAL 14 DAYS FOLLOWING
                ) AS ref_share
            FROM shares
        ),
        expect AS (
            SELECT *, ref_share * (day_rec - coalesce(rec, 0)) / (1 - ref_share) AS expected FROM ref
        ),
        fixed AS (
            -- An hour is a feed error only if it sits SPIKE_SIGMAS above what the rest of its
            -- day implies, allowing for Poisson noise plus 20% hour-to-hour variation.
            SELECT *,
                rec IS NULL AS was_gap,
                rec IS NOT NULL
                    AND rec > expected + {SPIKE_SIGMAS} * sqrt(expected + 0.04 * expected * expected)
                    AS was_spike
            FROM expect
        ),
        filled AS (
            SELECT *,
                CASE WHEN was_gap OR was_spike THEN coalesce(expected, 0) ELSE rec END AS orders_clean
            FROM fixed
        ),
        daily AS (
            SELECT date, hub, sum(orders_clean) AS day_total FROM filled GROUP BY ALL
        ),
        flagged AS (
            SELECT *,
                day_total < {OUTAGE_RATIO} * median(day_total) OVER (
                    PARTITION BY hub ORDER BY date
                    RANGE BETWEEN INTERVAL 28 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING
                ) AS outage
            FROM daily
        )
        SELECT f.date, f.hub, f.city, f.op_hour, f.regime, f.event_day, f.orders_clean,
               f.was_gap, f.was_spike, coalesce(g.outage, false) AS outage
        FROM filled f JOIN flagged g USING (date, hub)
        ORDER BY f.hub, f.date, f.op_hour
        """
    ).df()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out


def daily_orders(clean_hourly: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect()
    con.register("ch", clean_hourly)
    df = con.execute(
        """
        SELECT date, hub, city, any_value(regime) AS regime, bool_or(event_day) AS event_day,
               bool_or(outage) AS outage, sum(orders_clean) AS orders
        FROM ch GROUP BY date, hub, city ORDER BY hub, date
        """
    ).df()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def hourly_profiles(clean_hourly: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Share of a day's orders falling in each operating hour, by city and kind of day,
    estimated only from days before the cutoff."""
    con = duckdb.connect()
    con.register("ch", clean_hourly)
    df = con.execute(
        """
        WITH x AS (
            SELECT city, regime, op_hour, sum(orders_clean) AS n
            FROM ch WHERE date < ? AND NOT outage GROUP BY ALL
        )
        SELECT city, regime, op_hour, n / sum(n) OVER (PARTITION BY city, regime) AS share
        FROM x ORDER BY city, regime, op_hour
        """,
        [cutoff],
    ).df()
    return df


def cleaning_report(clean_hourly: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect()
    con.register("ch", clean_hourly)
    return con.execute(
        """
        SELECT count(*) AS hub_hours,
               sum(was_gap::INT) AS gaps_filled,
               sum(was_spike::INT) AS spikes_replaced,
               count(DISTINCT CASE WHEN outage THEN hub || date::VARCHAR END) AS outage_hub_days
        FROM ch
        """
    ).df()
