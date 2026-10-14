"""Operating constants: hubs, rider economics, shift menu and the event calendar.

Every number the planner, the replay and the generator share lives here, so a change to rider
cost or the promise window moves all three together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Hubs run 07:00 to 03:00. Operating hour 0 is 07:00 and hour 19 is 02:00 the next morning;
# demand between 03:00 and 07:00 is negligible and the hubs are closed.
OPEN_CLOCK_HOUR = 7
N_HOURS = 20

PROMISE_MINUTES = 60.0  # delivered within an hour of the order being placed
PICK_MINUTES = 6.0  # order is ready for a rider this long after it is placed
ONE_WAY_SHARE = 0.45  # share of the round trip spent before the handover


@dataclass(frozen=True)
class Hub:
    hub: str
    city: str
    base_daily: float  # ordinary-weekday orders at the start of 2025
    trip_minutes: float  # mean round trip: hub -> customer -> hub, handover included


HUBS: tuple[Hub, ...] = (
    Hub("A1", "A", 1300, 27.0),
    Hub("A2", "A", 1150, 25.0),
    Hub("A3", "A", 1050, 28.0),
    Hub("A4", "A", 950, 26.0),
    Hub("A5", "A", 900, 24.0),
    Hub("B1", "B", 1000, 24.0),
    Hub("B2", "B", 900, 23.0),
    Hub("B3", "B", 800, 25.0),
    Hub("B4", "B", 700, 22.0),
    Hub("C1", "C", 700, 21.0),
    Hub("C2", "C", 600, 22.0),
    Hub("C3", "C", 500, 20.0),
)
HUB_BY_ID = {h.hub: h for h in HUBS}
CITIES = ("A", "B", "C")

TRIP_CV = 0.35  # coefficient of variation of the round trip (lognormal)
IFTAR_TRAFFIC = 1.25  # trips are this much slower 16:00-19:00 during Ramadan
IFTAR_CLOCK_HOURS = (16, 17, 18)


# Rider economics, AED, fully loaded (pay, bike, fuel, insurance).
OWN_SHIFT_HOURS = 8
OWN_SHIFT_COST = 160.0  # 20 per hour
GIG_BLOCK_HOURS = 4
GIG_BLOCK_COST = 130.0  # 32.5 per hour: agency riders cost a premium and can be booked in blocks
LOST_ORDER_COST = 30.0  # gross margin plus a share of the customer's lifetime value
LATE_ORDER_COST = 0.01  # tie-breaker only; lateness is governed by the on-time constraint

OWN_STARTS = (0, 2, 4, 6, 8, 10, 12)  # 07:00 ... 19:00
GIG_STARTS = tuple(range(0, N_HOURS - GIG_BLOCK_HOURS + 1, 2))  # agencies book on even hours

OWN_NO_SHOW = 0.03
GIG_NO_SHOW = 0.08

# A customer who finds the hour sold out takes the next hour with this probability; otherwise
# the order is lost.
SLOT_RETRY = 0.7

# Own riders work six days in seven, so on any day this share of the pool is available.
OWN_AVAILABILITY = 6 / 7

# Hub parking and handover space caps how many riders can be on duty at once, expressed as a
# multiple of the riders an ordinary weekday's busiest hour needs.
SPACE_MULTIPLE = 3.0


# --- calendar ---------------------------------------------------------------------------

# First day of Ramadan and the day of Eid al-Fitr, as observed locally.
RAMADAN: dict[int, tuple[date, date]] = {
    2023: (date(2023, 3, 23), date(2023, 4, 21)),
    2024: (date(2024, 3, 11), date(2024, 4, 10)),
    2025: (date(2025, 3, 1), date(2025, 3, 30)),
    2026: (date(2026, 2, 18), date(2026, 3, 20)),
}
EID_ADHA: dict[int, date] = {
    2023: date(2023, 6, 28),
    2024: date(2024, 6, 16),
    2025: date(2025, 6, 6),
    2026: date(2026, 5, 27),
}
NATIONAL_DAY = (12, 2)


def mega_sale_day(year: int) -> date:
    """The November mega-sale lands on the last Friday of November."""
    d = date(year, 11, 30)
    return d - timedelta(days=(d.weekday() - 4) % 7)


# Peak windows the planner is evaluated on. Forecasts are made as of the cutoff, using only
# data recorded before it.
@dataclass(frozen=True)
class Window:
    name: str
    cutoff: date
    start: date
    end: date  # inclusive


WINDOWS: tuple[Window, ...] = (
    Window("ramadan_2025", date(2025, 1, 31), date(2025, 2, 24), date(2025, 4, 3)),
    Window("mega_sale_2025", date(2025, 10, 31), date(2025, 11, 20), date(2025, 12, 1)),
)

DATA_START = date(2023, 1, 1)
DATA_END = date(2025, 12, 31)
