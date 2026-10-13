"""The replay is the yardstick for every plan, so it is checked against queueing theory first."""

from __future__ import annotations

import math

import numpy as np
import pytest

from peak_capacity.config import N_HOURS, SLOT_RETRY
from peak_capacity.replay import dispatch, replay_hub_day


def erlang_c(c: int, a: float) -> float:
    """Probability an arrival waits in an M/M/c queue with offered load a = lambda / mu."""
    top = a**c / math.factorial(c) * c / (c - a)
    return top / (sum(a**k / math.factorial(k) for k in range(c)) + top)


def test_dispatch_matches_erlang_c_waiting_probabilities() -> None:
    rng = np.random.default_rng(3)
    c, mean_trip, lam = 10, 25.0, 0.32  # utilisation 0.8
    n = 40_000
    ready = np.cumsum(rng.exponential(1 / lam, n))
    trip = rng.exponential(mean_trip, n)
    depart, _ = dispatch(ready, np.zeros(c), np.full(c, np.inf), trip)
    wait = (depart - ready)[2_000:]  # drop the warm-up from an empty system
    p_wait = erlang_c(c, lam * mean_trip)
    excess = c / mean_trip - lam
    assert np.mean(wait > 1e-9) == pytest.approx(p_wait, abs=0.03)
    assert np.mean(wait > 10) == pytest.approx(p_wait * math.exp(-excess * 10), abs=0.03)


def test_rider_never_starts_a_trip_after_shift_end() -> None:
    ready = np.array([10.0, 20.0, 70.0])
    depart, _ = dispatch(ready, np.array([0.0]), np.array([60.0]), np.array([30.0, 30.0, 30.0]))
    assert depart[0] == 10.0
    assert depart[1] == 40.0  # waits for the rider to come back
    assert np.isnan(depart[2])  # shift is over


def _flat(n: int) -> np.ndarray:
    return np.full(N_HOURS, n)


def test_no_riders_means_nothing_is_delivered() -> None:
    r = replay_hub_day(_flat(20), np.full(N_HOURS, np.inf), {}, {}, 25.0, np.ones(N_HOURS), (1,))
    assert r.accepted == r.demand == 20 * N_HOURS
    assert r.on_time == 0 and r.undelivered == r.accepted


def test_ample_riders_deliver_almost_everything_on_time() -> None:
    own = {0: 30, 6: 30, 12: 30}  # covers every hour from open to close
    r = replay_hub_day(_flat(20), np.full(N_HOURS, np.inf), own, {}, 25.0, np.ones(N_HOURS), (2,))
    assert r.otd > 0.99
    assert r.lost == 0


def test_closed_slots_turn_customers_away_and_some_retry() -> None:
    cap = np.full(N_HOURS, np.inf)
    cap[5] = 0
    demand = np.zeros(N_HOURS, dtype=int)
    demand[5] = 1000
    r = replay_hub_day(demand, cap, {0: 50, 12: 50}, {}, 25.0, np.ones(N_HOURS), (4,))
    assert r.accepted == pytest.approx(1000 * SLOT_RETRY, abs=50)
    assert r.lost + r.accepted == 1000


def test_same_seed_same_result() -> None:
    args = (_flat(30), np.full(N_HOURS, 40.0), {0: 10, 8: 10}, {4: 5}, 25.0, np.ones(N_HOURS), (9, 1))
    assert replay_hub_day(*args) == replay_hub_day(*args)
