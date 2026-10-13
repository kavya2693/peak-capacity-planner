"""Trip times shared by the planner (as a rate) and the replay (as a distribution)."""

from __future__ import annotations

import numpy as np

from peak_capacity.config import IFTAR_CLOCK_HOURS, IFTAR_TRAFFIC, N_HOURS, OPEN_CLOCK_HOUR


def traffic(regime: str) -> np.ndarray:
    """Multiplier on trip time per operating hour. Roads jam in the hours before iftar."""
    f = np.ones(N_HOURS)
    if regime == "ramadan":
        for clock in IFTAR_CLOCK_HOURS:
            f[clock - OPEN_CLOCK_HOUR] = IFTAR_TRAFFIC
    return f


def fluid_rate(trip_minutes: float, regime: str) -> np.ndarray:
    """Orders one rider delivers per hour if never idle."""
    return 60.0 / (trip_minutes * traffic(regime))
