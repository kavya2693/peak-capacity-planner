"""Inline exported JSON into a composition (renders cannot fetch files).

Replaces the `/* <MARKER>:BEGIN */ ... /* <MARKER>:END */` block with `const <MARKER> = {...};`.

  # slide 07: sale-day roster, 6 hubs shown as rows, 3 of them animated in the 2D scene
  python3 tools/inject_sheet.py assets/sale_day_roster.json compositions/value.html \
      --marker SHEET --rows A1,A2,A4,B1,B2,C1 --animate A1,B2,C1
  # slide 10: sale-day network forecast per hour (backdrop bars)
  python3 tools/inject_sheet.py assets/sale_day_roster.json compositions/s07-end.html --marker SALE
  # slide 06: daily replay series (passed through unchanged)
  python3 tools/inject_sheet.py assets/run_series.json compositions/s04-routes.html --marker SERIES
"""

import argparse
import json
import re

ap = argparse.ArgumentParser()
ap.add_argument("json_file")
ap.add_argument("html")
ap.add_argument("--marker", default="SHEET")
ap.add_argument("--rows", help="hub ids shown as sheet rows, in order (SHEET only)")
ap.add_argument("--animate", help="hub ids drawn in the 2D scene, max 3 (SHEET only)")
a = ap.parse_args()

with open(a.json_file) as fh:
    d = json.load(fh)

if a.marker == "SHEET":
    by_hub = {h["hub"]: h for h in d["hubs"]}
    rows = a.rows.split(",")
    anim = a.animate.split(",")
    keep = (
        "hub",
        "city",
        "own_riders",
        "gig_total",
        "peak_riders",
        "max_on_duty",
        "busiest_hour",
        "busiest_forecast",
        "busiest_slots",
        "otd",
    )
    payload = {
        "day": d["day"],
        "hours": d["hours"],
        "totals": d["totals"],
        "n_hubs": len(d["hubs"]),
        "rows": [
            {k: by_hub[h][k] for k in keep} | {"animate": anim.index(h) if h in anim else -1} for h in rows
        ],
        "scene": [
            {k: by_hub[h][k] for k in ("hub", "riders_on_duty", "max_on_duty", "forecast", "slot_cap", "otd")}
            for h in anim
        ],
    }
elif a.marker == "SALE":
    # network-wide forecast orders per operating hour on the sale day (end-card backdrop)
    payload = [round(sum(h["forecast"][t] for h in d["hubs"]), 1) for t in range(len(d["hours"]))]
else:
    payload = d

with open(a.html) as fh:
    html = fh.read()
m = a.marker
block = f"/* {m}:BEGIN */ const {m} = " + json.dumps(payload, separators=(",", ":")) + f"; /* {m}:END */"
html, n = re.subn(rf"/\* {m}:BEGIN \*/.*?/\* {m}:END \*/", lambda _: block, html, flags=re.S)
assert n == 1, "marker not found"
with open(a.html, "w") as fh:
    fh.write(html)
print("injected", m, "into", a.html)
