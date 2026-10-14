"""Forecast, plan and replay every configuration over both peak windows.

A configuration is a forecaster, a planning method, an on-time target and a capacity derate.
Every configuration is replayed on the same true demand with the same random seeds per
hub-day, so differences between rows come from the plan and not from luck.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from peak_capacity.calendar import day_features, regime
from peak_capacity.config import (
    CITIES,
    LOST_ORDER_COST,
    N_HOURS,
    OWN_AVAILABILITY,
    WINDOWS,
    Window,
)
from peak_capacity.forecast import accuracy, event_model, seasonal_naive
from peak_capacity.plan import CityDayPlan, plan_city_day, rule_of_thumb
from peak_capacity.replay import replay_hub_day
from peak_capacity.service import fluid_rate, traffic


@dataclass(frozen=True)
class Config:
    name: str
    forecaster: str  # "model" | "naive" | "oracle"
    method: str  # "mip" | "rule"
    target: float = 0.99
    derate: float = 1.0
    cap_rule: str = "fluid"  # slot cap at flat-out rider throughput, or "derated"


def segment(d: date) -> str:
    f = day_features(d)
    if f["sale_offset"] == 0:
        return "sale_day"
    if f["sale_offset"] != 99:
        return "sale_week"
    if f["eid_fitr_offset"] != 99:
        return "eid"
    if f["ramadan_last10"]:
        return "ramadan_last10"
    if f["ramadan_day"] > 0:
        return "ramadan"
    return "ordinary"


def default_configs(quick: bool) -> list[Config]:
    if quick:
        return [
            Config("rule_of_thumb", "model", "rule"),
            Config("mip_t99_d100", "model", "mip", 0.99, 1.0),
            Config("mip_t99_d85", "model", "mip", 0.99, 0.85),
        ]
    configs = [Config("rule_of_thumb", "model", "rule")]
    for derate in (1.0, 0.85):
        for target in (0.95, 0.97, 0.99, 0.995):
            configs.append(Config(f"mip_t{target * 100:g}_d{derate * 100:g}", "model", "mip", target, derate))
    configs += [
        Config("mip_t99_d85_naive", "naive", "mip", 0.99, 0.85),
        Config("mip_t99_d100_oracle", "oracle", "mip", 0.99, 1.0),
        Config("mip_t99_d85_oracle", "oracle", "mip", 0.99, 0.85),
        Config("mip_t99_d85_derated_caps", "model", "mip", 0.99, 0.85, "derated"),
    ]
    return configs


@dataclass(frozen=True)
class CityDayTask:
    config: Config
    city: str
    day: date
    demand: dict[str, np.ndarray]
    trip_minutes: dict[str, float]
    max_on_duty: dict[str, int]
    own_available: int


def _solve(task: CityDayTask) -> tuple[CityDayTask, CityDayPlan]:
    reg = regime(day_features(task.day))
    if task.config.method == "rule":
        plan = rule_of_thumb(task.city, task.demand, task.trip_minutes, task.max_on_duty, task.own_available)
    else:
        rate = {h: fluid_rate(m, reg) for h, m in task.trip_minutes.items()}
        plan = plan_city_day(
            task.city,
            task.demand,
            rate,
            task.max_on_duty,
            task.own_available,
            task.config.target,
            task.config.derate,
            task.config.cap_rule,
        )
    return task, plan


def window_days(w: Window, quick: bool) -> list[date]:
    days = [w.start + timedelta(days=i) for i in range((w.end - w.start).days + 1)]
    if quick:
        days = [d for d in days if segment(d) in ("sale_day", "ramadan_last10")][:3]
    return days


def _as_matrix(fc: pd.DataFrame, col: str) -> dict[tuple[str, date], np.ndarray]:
    return {
        key: g.sort_values("op_hour")[col].to_numpy(dtype=float) for key, g in fc.groupby(["hub", "date"])
    }


def run(
    hourly: pd.DataFrame,
    clean_hourly: pd.DataFrame,
    daily: pd.DataFrame,
    hubs: pd.DataFrame,
    cities: pd.DataFrame,
    configs: Iterable[Config],
    quick: bool = False,
    workers: int = 1,
    seed: int = 0,
    log=print,
) -> dict[str, pd.DataFrame]:
    configs = list(configs)
    actual = hourly[["date", "hub", "op_hour", "orders"]]
    truth = _as_matrix(actual, "orders")
    trip = dict(zip(hubs["hub"], hubs["trip_minutes"], strict=True))
    max_on = dict(zip(hubs["hub"], hubs["max_on_duty"], strict=True))
    own_avail = {r.city: math.floor(r.own_pool * OWN_AVAILABILITY) for r in cities.itertuples()}
    hub_index = {h: i for i, h in enumerate(hubs["hub"])}

    acc_rows, tasks = [], []
    for w in WINDOWS:
        days = window_days(w, quick)
        fcs = {
            "model": event_model(daily, clean_hourly, w, seed),
            "naive": seasonal_naive(clean_hourly, daily, w),
        }
        seg = pd.Series({d: segment(d) for d in days})
        for name, fc in fcs.items():
            fc_days = fc[fc["date"].isin(days)]
            a = accuracy(fc_days, actual, seg)
            acc_rows.append(a.assign(window=w.name, forecaster=name))
        mats = {k: _as_matrix(v[v["date"].isin(days)], "forecast") for k, v in fcs.items()}
        mats["oracle"] = {k: v for k, v in truth.items() if k[1] in set(days)}
        for cfg in configs:
            for d in days:
                for c in CITIES:
                    hs = hubs.loc[hubs["city"] == c, "hub"].tolist()
                    tasks.append(
                        CityDayTask(
                            config=cfg,
                            city=c,
                            day=d,
                            demand={h: mats[cfg.forecaster][(h, d)] for h in hs},
                            trip_minutes={h: trip[h] for h in hs},
                            max_on_duty={h: max_on[h] for h in hs},
                            own_available=own_avail[c],
                        )
                    )
    log(f"solving {len(tasks)} city-day plans for {len(configs)} configurations with {workers} workers")

    solved: list[tuple[CityDayTask, CityDayPlan]] = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for result in ex.map(_solve, tasks, chunksize=4):
            solved.append(result)
            if len(solved) % 200 == 0:
                log(f"  solved {len(solved)}/{len(tasks)} city-day plans")

    rows = []
    for n, (task, plan) in enumerate(solved, 1):
        reg = regime(day_features(task.day))
        traf = traffic(reg)
        for hp in plan.hubs:
            res = replay_hub_day(
                truth[(hp.hub, task.day)],
                hp.slot_cap,
                hp.own,
                hp.gig,
                trip[hp.hub],
                traf,
                seed=(seed, hub_index[hp.hub], task.day.toordinal()),
            )
            rows.append(
                {
                    "config": task.config.name,
                    **{k: v for k, v in asdict(task.config).items() if k != "name"},
                    "date": task.day,
                    "segment": segment(task.day),
                    "city": task.city,
                    "hub": hp.hub,
                    "solver_status": plan.status,
                    "own_riders": sum(hp.own.values()),
                    "gig_blocks": sum(hp.gig.values()),
                    "peak_riders": int(hp.riders.max()),
                    "rider_cost": hp.rider_cost,
                    "planned_accepted": hp.accepted,
                    "planned_on_time": hp.on_time,
                    "planned_lost": hp.lost,
                    "forecast_orders": float(task.demand[hp.hub].sum()),
                    **asdict(res),
                }
            )
        if n % 200 == 0:
            log(f"  replayed {n}/{len(solved)} city-days")
    hub_day = pd.DataFrame(rows)
    return {
        "forecast_accuracy": pd.concat(acc_rows, ignore_index=True),
        "hub_day": hub_day,
        **summarise(hub_day),
    }


def _agg(g: pd.DataFrame) -> pd.Series:
    delivered = g["on_time"].sum() + g["late"].sum()
    lost_cost = LOST_ORDER_COST * g["lost"].sum()
    return pd.Series(
        {
            "hub_days": len(g),
            "rider_cost": g["rider_cost"].sum(),
            "lost_margin": lost_cost,
            "total_cost": g["rider_cost"].sum() + lost_cost,
            "planned_otd": g["planned_on_time"].sum(min_count=len(g)) / g["planned_accepted"].sum(),
            "replay_otd": g["on_time"].sum() / g["accepted"].sum(),
            "served_on_time_share": g["on_time"].sum() / g["demand"].sum(),
            "lost_share": g["lost"].sum() / g["demand"].sum(),
            "undelivered": g["undelivered"].sum(),
            "cost_per_delivered": (g["rider_cost"].sum() + lost_cost) / max(delivered, 1),
            "hub_days_below_target": float(g["below_target"].mean()),
            "p90_minutes": g["p90_minutes"].median(),
        }
    )


def summarise(hub_day: pd.DataFrame) -> dict[str, pd.DataFrame]:
    keys = ["config", "forecaster", "method", "target", "derate", "cap_rule"]
    hub_day = hub_day.assign(below_target=hub_day["on_time"] / hub_day["accepted"] < hub_day["target"])
    summary = hub_day.groupby(keys).apply(_agg, include_groups=False).reset_index()
    by_segment = hub_day.groupby([*keys, "segment"]).apply(_agg, include_groups=False).reset_index()
    tradeoff = (
        summary[(summary["method"] == "mip") & (summary["forecaster"] == "model")]
        .sort_values(["derate", "target"])
        .reset_index(drop=True)
    )
    tradeoff["extra_cost_vs_prev_target"] = tradeoff.groupby("derate")["total_cost"].diff()
    tradeoff["replay_otd_gain_pts"] = tradeoff.groupby("derate")["replay_otd"].diff() * 100
    return {"plan_summary": summary, "by_segment": by_segment, "tradeoff": tradeoff}


__all__ = ["N_HOURS", "Config", "default_configs", "run", "segment"]
