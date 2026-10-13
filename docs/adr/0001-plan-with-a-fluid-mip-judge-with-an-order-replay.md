# ADR-0001: Plan with an hourly MIP, judge the plan with an order-level replay

## Status
Accepted

## Context
Peak planning for the delivery network has to answer one question per hub and day: how many
riders on which shifts, and how many orders to accept each hour, so that the hub keeps its
one-hour promise at the lowest cost. Three things make that easy to get wrong:

1. The demand that matters moves. Ramadan starts about eleven days earlier every year, so the
   busiest nights land on different Gregorian dates, and the November mega-sale grows every
   year. A plan copied from last year is aligned to the wrong days and sized to a smaller sale.
2. An on-time target is a statement about individual orders, but any model that can be
   optimised over hundreds of hub-days reasons in hourly averages. Averages hide queues: at
   high utilisation, random trip times make orders wait even when the hour has enough rider
   capacity on paper.
3. On the sale day the hubs physically run out of room for riders. Past that point the only
   lever left is how many orders the storefront accepts, and a high on-time share can be
   bought by turning customers away.

## Decision
- **Forecast from the events, not the dates.** Daily demand per hub is modelled as a multiple
  of the hub's recent ordinary level, with features counted from the event (day of Ramadan,
  days to Eid, days to the sale). Hours come from the observed shape of that kind of day.
  A same-weekday-last-year forecast is kept as the baseline.
- **Plan each city-day with a mixed-integer program (PuLP + CBC).** Integer own-rider shifts
  and agency blocks per hub, continuous accepted/on-time/late/backlog flows per hour, an own
  rider pool shared by the hubs of a city, a cap on riders on duty per hub, and an on-time
  constraint per hub-day. Objective: rider cost plus the margin lost on refused orders.
- **Judge every plan in an order-level replay that shares nothing with the planner except the
  roster, the slot caps and the mean trip time.** Orders arrive at random minutes, trips are
  lognormal, riders no-show, and on time means delivered within 60 minutes of placing the
  order. The replay's dispatcher is tested against the Erlang C formula before any plan is
  scored with it.
- **Treat the gap between planned and replayed on-time as a measured quantity and close it
  with one parameter**, a derate on fluid rider throughput, chosen from the replay rather than
  assumed.
- **Report two on-time numbers**: on-time share of accepted orders (what the operation
  reports) and on-time share of everyone who tried to order (which closing slots cannot
  flatter).

## Alternatives rejected
- **Queueing formulas (Erlang C) inside the optimiser.** Exact for a stationary hour, but
  non-linear in riders, and the hours are not stationary: backlog carries from one hour to the
  next. The formulas are used to test the replay instead.
- **Stochastic programming over demand scenarios.** The right tool for forecast error, but it
  multiplies the model size by the scenario count and still leaves the queueing gap. The
  derate is cruder and is measured, not assumed.
- **Planning each hub on its own.** Simpler and faster, but the own-rider pool is shared by
  the hubs of a city; planning hubs separately double-books riders on the days that matter.
- **A single on-time metric.** Rejected because the optimiser can reach any on-time target by
  refusing orders, and the first replays did exactly that on the sale day.

## Consequences
- CBC is run with a 0.5% optimality gap and a 10-second limit per city-day so the full grid
  (17 configurations, 51 days, 3 cities) runs in tens of minutes. Differences between plans
  smaller than about 0.5% of cost are noise.
- The derate is one number for every hour and hub; it over-staffs quiet hours to protect busy
  ones. A per-utilisation derate is the obvious next step.
