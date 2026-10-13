"""Rider shifts and slot caps for one city-day, as a mixed-integer program solved with CBC.

Per hub and operating hour the model decides how many own riders start an 8-hour shift and how
many agency riders are booked for 4-hour blocks, and how many orders the storefront accepts.
Orders that cannot be delivered within their hour carry over as late; a customer who finds an
hour sold out retries the next hour or is lost. The hub must deliver at least `target` of what
it accepts within the hour, at minimum rider cost plus the margin lost on turned-away orders.

The capacity model is a fluid one: a rider delivers 60 / trip_minutes orders an hour, every
hour. `derate` scales that down, because queues with random trip times never run at the fluid
rate; the replay measures how much.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pulp

from peak_capacity.config import (
    GIG_BLOCK_COST,
    GIG_BLOCK_HOURS,
    GIG_STARTS,
    LATE_ORDER_COST,
    LOST_ORDER_COST,
    N_HOURS,
    OWN_SHIFT_COST,
    OWN_SHIFT_HOURS,
    OWN_STARTS,
    SLOT_RETRY,
)


@dataclass
class HubPlan:
    hub: str
    own: dict[int, int]  # start hour -> riders
    gig: dict[int, int]
    riders: np.ndarray  # on duty per hour
    slot_cap: np.ndarray  # orders the storefront accepts per hour
    accepted: float
    on_time: float
    lost: float
    rider_cost: float

    @property
    def planned_otd(self) -> float:
        return self.on_time / self.accepted if self.accepted else 1.0


@dataclass
class CityDayPlan:
    city: str
    status: str
    hubs: list[HubPlan] = field(default_factory=list)

    @property
    def rider_cost(self) -> float:
        return sum(h.rider_cost for h in self.hubs)


def riders_on_duty(own: dict[int, int], gig: dict[int, int]) -> np.ndarray:
    r = np.zeros(N_HOURS)
    for s, n in own.items():
        r[s : s + OWN_SHIFT_HOURS] += n
    for s, n in gig.items():
        r[s : s + GIG_BLOCK_HOURS] += n
    return r


def plan_city_day(
    city: str,
    demand: dict[str, np.ndarray],
    rate: dict[str, np.ndarray],
    max_on_duty: dict[str, int],
    own_available: int,
    target: float,
    derate: float = 1.0,
    time_limit: int = 10,
) -> CityDayPlan:
    """`demand[h][t]` expected orders, `rate[h][t]` orders per rider-hour at fluid speed."""
    prob = pulp.LpProblem(f"plan_{city}", pulp.LpMinimize)
    hubs = list(demand)
    T = range(N_HOURS)
    own = {(h, s): pulp.LpVariable(f"own_{h}_{s}", 0, cat="Integer") for h in hubs for s in OWN_STARTS}
    gig = {(h, s): pulp.LpVariable(f"gig_{h}_{s}", 0, cat="Integer") for h in hubs for s in GIG_STARTS}

    def cont(name: str) -> dict[tuple[str, int], pulp.LpVariable]:
        return {(h, t): pulp.LpVariable(f"{name}_{h}_{t}", 0) for h in hubs for t in T}

    acc, on, late, back, spill = cont("acc"), cont("on"), cont("late"), cont("back"), cont("spill")

    cost_terms = []
    for h in hubs:
        riders = {
            t: pulp.lpSum(own[h, s] for s in OWN_STARTS if s <= t < s + OWN_SHIFT_HOURS)
            + pulp.lpSum(gig[h, s] for s in GIG_STARTS if s <= t < s + GIG_BLOCK_HOURS)
            for t in T
        }
        for t in T:
            arrivals = demand[h][t] + (SLOT_RETRY * spill[h, t - 1] if t > 0 else 0)
            prob += spill[h, t] == arrivals - acc[h, t]
            prob += on[h, t] <= acc[h, t]
            prob += on[h, t] + late[h, t] <= derate * rate[h][t] * riders[t]
            prob += back[h, t] == (back[h, t - 1] if t > 0 else 0) + acc[h, t] - on[h, t] - late[h, t]
            prob += riders[t] <= max_on_duty[h]
        prob += back[h, N_HOURS - 1] == 0
        prob += pulp.lpSum(on[h, t] for t in T) >= target * pulp.lpSum(acc[h, t] for t in T)
        lost = (1 - SLOT_RETRY) * pulp.lpSum(spill[h, t] for t in T if t < N_HOURS - 1) + spill[
            h, N_HOURS - 1
        ]
        cost_terms += [
            OWN_SHIFT_COST * pulp.lpSum(own[h, s] for s in OWN_STARTS),
            GIG_BLOCK_COST * pulp.lpSum(gig[h, s] for s in GIG_STARTS),
            LOST_ORDER_COST * lost,
            LATE_ORDER_COST * pulp.lpSum(back[h, t] for t in T),
        ]
    prob += pulp.lpSum(own.values()) <= own_available
    prob += pulp.lpSum(cost_terms)
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=0.005, threads=1))

    plan = CityDayPlan(city=city, status=pulp.LpStatus[prob.status])
    for h in hubs:
        o = {s: round(own[h, s].value() or 0) for s in OWN_STARTS}
        g = {s: round(gig[h, s].value() or 0) for s in GIG_STARTS}
        r = riders_on_duty(o, g)
        a = np.array([acc[h, t].value() or 0.0 for t in T])
        spills = np.array([spill[h, t].value() or 0.0 for t in T])
        # The storefront keeps an hour open until the roster's throughput for that hour is sold.
        cap = np.maximum(a, derate * rate[h] * r)
        plan.hubs.append(
            HubPlan(
                hub=h,
                own={s: n for s, n in o.items() if n},
                gig={s: n for s, n in g.items() if n},
                riders=r,
                slot_cap=cap,
                accepted=float(a.sum()),
                on_time=float(sum(on[h, t].value() or 0.0 for t in T)),
                lost=float((1 - SLOT_RETRY) * spills[:-1].sum() + spills[-1]),
                rider_cost=OWN_SHIFT_COST * sum(o.values()) + GIG_BLOCK_COST * sum(g.values()),
            )
        )
    return plan


def rule_of_thumb(
    city: str,
    demand: dict[str, np.ndarray],
    trip_minutes: dict[str, float],
    max_on_duty: dict[str, int],
    own_available: int,
    utilisation: float = 0.7,
) -> CityDayPlan:
    """Last season's playbook: size own riders to the day's forecast at an assumed
    utilisation, spread them over three fixed shift starts, cover any shortfall of own riders
    with evening agency blocks, and never close a slot."""
    need = {
        h: math.ceil(demand[h].sum() * trip_minutes[h] / 60 / (OWN_SHIFT_HOURS * utilisation)) for h in demand
    }
    scale = min(1.0, own_available / max(1, sum(need.values())))
    plan = CityDayPlan(city=city, status="Heuristic")
    for h in demand:
        n_own = math.floor(need[h] * scale)
        shortfall_blocks = 2 * (need[h] - n_own)
        own = {0: n_own // 3, 4: n_own // 3, 10: n_own - 2 * (n_own // 3)}
        gig = {10: shortfall_blocks // 2, 14: shortfall_blocks - shortfall_blocks // 2}
        r = riders_on_duty(own, gig)
        # Agency riders beyond the hub's space are simply not booked.
        while (r > max_on_duty[h]).any() and (gig[10] or gig[14]):
            key = 14 if gig[14] >= gig[10] else 10
            gig[key] -= 1
            r = riders_on_duty(own, gig)
        plan.hubs.append(
            HubPlan(
                hub=h,
                own={s: n for s, n in own.items() if n},
                gig={s: n for s, n in gig.items() if n},
                riders=r,
                slot_cap=np.full(N_HOURS, np.inf),
                accepted=float(demand[h].sum()),
                on_time=float("nan"),
                lost=0.0,
                rider_cost=OWN_SHIFT_COST * sum(own.values()) + GIG_BLOCK_COST * sum(gig.values()),
            )
        )
    return plan
