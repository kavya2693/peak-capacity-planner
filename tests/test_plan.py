"""The planner must honour its own constraints; the replay judges whether that was enough."""

from __future__ import annotations

import itertools

import numpy as np

from peak_capacity.config import N_HOURS, OWN_SHIFT_COST
from peak_capacity.plan import plan_city_day, riders_on_duty, rule_of_thumb


def _demand(level: float) -> np.ndarray:
    shape = np.sin(np.linspace(0.2, np.pi - 0.2, N_HOURS)) + 0.2
    return level * shape / shape.sum()


def _plan(target: float, level: float = 600, max_on: int = 60, own_avail: int = 40, derate: float = 1.0):
    demand = {"H1": _demand(level), "H2": _demand(level * 0.6)}
    rate = {h: np.full(N_HOURS, 2.4) for h in demand}
    return plan_city_day("X", demand, rate, {"H1": max_on, "H2": max_on}, own_avail, target, derate)


def test_plan_meets_on_time_target_and_pool_limits() -> None:
    plan = _plan(0.99, own_avail=12)
    assert plan.status in ("Optimal Solution Found", "Solution Found")
    assert sum(sum(h.own.values()) for h in plan.hubs) <= 12
    for h in plan.hubs:
        assert h.planned_otd >= 0.99 - 1e-6
        assert (h.riders <= 60).all()


def test_raising_the_target_never_makes_the_plan_cheaper() -> None:
    costs = []
    for target in (0.9, 0.97, 0.995):
        p = _plan(target)
        costs.append(p.rider_cost + 30 * sum(h.lost for h in p.hubs))
    # CBC stops within 0.5% of optimal, so two plans that close can come out in either order.
    assert all(later >= earlier * (1 - 0.005) for earlier, later in itertools.pairwise(costs))
    assert costs[-1] > costs[0]


def test_space_limit_forces_slot_closures_not_extra_riders() -> None:
    plan = _plan(0.99, level=3000, max_on=20)
    hub = plan.hubs[0]
    assert hub.riders.max() <= 20
    assert hub.lost > 0
    # Where customers are turned away, the slot cap is what the full roster can carry.
    assert hub.slot_cap.min() <= 20 * 2.4 + 1e-6


def test_derate_buys_more_riders() -> None:
    assert _plan(0.99, derate=0.8).rider_cost > _plan(0.99, derate=1.0).rider_cost


def test_rule_of_thumb_respects_space_and_pool() -> None:
    demand = {"H1": _demand(4000)}
    plan = rule_of_thumb("X", demand, {"H1": 25.0}, {"H1": 50}, own_available=30)
    hub = plan.hubs[0]
    assert sum(hub.own.values()) <= 30
    assert hub.riders.max() <= 50
    assert np.isinf(hub.slot_cap).all()


def test_riders_on_duty_counts_shift_coverage() -> None:
    r = riders_on_duty({0: 2, 12: 1}, {4: 3})
    assert r[0] == 2 and r[4] == 5 and r[8] == 0 and r[12] == 1 and r[19] == 1
    assert r.sum() == 2 * 8 + 1 * 8 + 3 * 4
    assert OWN_SHIFT_COST > 0
