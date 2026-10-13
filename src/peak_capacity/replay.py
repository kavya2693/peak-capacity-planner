"""Order-by-order replay of a plan against a day's demand.

The planner reasons about hourly averages. The replay plays individual orders: each arrives at
a random minute, the storefront accepts it only while the hour has slots left, it is ready
`PICK_MINUTES` later, and it leaves with whichever on-duty rider can take it first. Trip times
are random, some rostered riders do not show up, and a rider never starts a trip after their
shift ends. An order is on time if it reaches the customer within `PROMISE_MINUTES` of being
placed. This is the check on the plan, so it shares nothing with the planner except the
roster, the slot caps and the mean trip time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from peak_capacity.config import (
    GIG_BLOCK_HOURS,
    GIG_NO_SHOW,
    N_HOURS,
    ONE_WAY_SHARE,
    OWN_NO_SHOW,
    OWN_SHIFT_HOURS,
    PICK_MINUTES,
    PROMISE_MINUTES,
    SLOT_RETRY,
    TRIP_CV,
)

CLOSE_MINUTE = 60 * N_HOURS
# The storefront stops taking orders this long before close, so the last order can still leave.
LAST_ORDER_BUFFER = 15.0


def lognormal_factors(rng: np.random.Generator, n: int, cv: float = TRIP_CV) -> np.ndarray:
    """Multiplicative trip-time noise with mean 1."""
    sigma = np.sqrt(np.log1p(cv**2))
    return rng.lognormal(-(sigma**2) / 2, sigma, n)


def dispatch(
    ready: np.ndarray,
    rider_start: np.ndarray,
    rider_end: np.ndarray,
    trip: np.ndarray,
    traffic: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """First-come first-served assignment of orders (sorted by `ready`) to riders.

    Each order goes to the rider who can leave with it soonest. `trip[i]` is order i's round
    trip at free-flow speed; `traffic[h]` stretches trips that start in operating hour h.
    Returns departure and rider-busy-until per order; NaN where no rider could take it.
    """
    free = rider_start.astype(float).copy()
    depart = np.full(len(ready), np.nan)
    busy_until = np.full(len(ready), np.nan)
    if len(free) == 0:
        return depart, busy_until
    for i, r in enumerate(ready):
        cand = np.maximum(free, r)
        cand[cand >= rider_end] = np.inf
        k = int(np.argmin(cand))
        if not np.isfinite(cand[k]):
            continue
        d = cand[k]
        mult = 1.0 if traffic is None else traffic[min(int(d // 60), len(traffic) - 1)]
        depart[i] = d
        busy_until[i] = d + trip[i] * mult
        free[k] = busy_until[i]
    return depart, busy_until


@dataclass
class ReplayResult:
    demand: int  # customers who wanted to order
    accepted: int
    lost: int
    on_time: int
    late: int  # delivered after the promise
    undelivered: int  # accepted but no rider could take it before close
    rider_hours: float  # hours of riders who actually turned up
    p90_minutes: float

    @property
    def otd(self) -> float:
        """On-time share of accepted orders: the number the operation reports."""
        return self.on_time / self.accepted if self.accepted else 1.0

    @property
    def served_on_time_share(self) -> float:
        """On-time share of everyone who tried to order. Closing slots cannot flatter this one."""
        return self.on_time / self.demand if self.demand else 1.0


def replay_hub_day(
    hourly_demand: np.ndarray,
    slot_cap: np.ndarray,
    own: dict[int, int],
    gig: dict[int, int],
    trip_minutes: float,
    traffic: np.ndarray,
    seed: tuple[int, ...],
) -> ReplayResult:
    ss = np.random.SeedSequence(list(seed))
    rng_arr, rng_trip, rng_show = (np.random.default_rng(s) for s in ss.spawn(3))

    # Arrivals hour by hour; a customer refused by a full hour may retry the next one.
    placed: list[np.ndarray] = []
    lost = 0
    carry = 0
    for t in range(N_HOURS):
        n_new = int(hourly_demand[t])
        width = 60.0 - (LAST_ORDER_BUFFER if t == N_HOURS - 1 else 0.0)
        times = np.sort(60 * t + width * rng_arr.random(n_new + carry))
        cap = slot_cap[t]
        n_acc = len(times) if not np.isfinite(cap) else min(len(times), int(np.floor(cap + 1e-9)))
        placed.append(times[:n_acc])
        refused = len(times) - n_acc
        if t == N_HOURS - 1:
            lost += refused
            carry = 0
        else:
            carry = int(rng_arr.binomial(refused, SLOT_RETRY))
            lost += refused - carry
    orders = np.concatenate(placed) if placed else np.zeros(0)

    # Roster after no-shows.
    starts, ends = [], []
    for shifts, hours, p_absent in ((own, OWN_SHIFT_HOURS, OWN_NO_SHOW), (gig, GIG_BLOCK_HOURS, GIG_NO_SHOW)):
        for s, n in sorted(shifts.items()):
            present = int((rng_show.random(n) >= p_absent).sum())
            starts += [60 * s] * present
            ends += [60 * (s + hours)] * present
    rider_start, rider_end = np.array(starts, dtype=float), np.array(ends, dtype=float)

    trip = trip_minutes * lognormal_factors(rng_trip, len(orders))
    depart, _ = dispatch(orders + PICK_MINUTES, rider_start, rider_end, trip, traffic)
    delivered = depart + ONE_WAY_SHARE * trip * traffic[np.minimum(np.nan_to_num(depart) // 60, N_HOURS - 1).astype(int)]
    lead = delivered - orders
    ok = ~np.isnan(lead)
    on_time = int((lead[ok] <= PROMISE_MINUTES).sum())
    return ReplayResult(
        demand=int(np.sum(hourly_demand)),
        accepted=len(orders),
        lost=lost,
        on_time=on_time,
        late=int(ok.sum()) - on_time,
        undelivered=int((~ok).sum()),
        rider_hours=float((rider_end - rider_start).sum() / 60),
        p90_minutes=float(np.percentile(lead[ok], 90)) if ok.any() else float("nan"),
    )
