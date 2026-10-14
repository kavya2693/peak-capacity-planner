# Video and audit report sources

Everything needed to rebuild the silent walkthrough (`docs/demo/demo.mp4`) and the audit report
(`docs/demo/AUDIT_REPORT.*`) from this repository alone. Paths below are relative to the repo root.

## What is here

| Path | What it is |
|---|---|
| `index.html` | Root of the video: theme (colours, type), and the timing of every slide |
| `compositions/bg.html`, `compositions/chrome.html` | Background grid, and the persistent project label, slide counter and progress bar |
| `compositions/s01-context.html` | Slide 01, the industry and four everyday problems |
| `compositions/s02-hook.html` | Slide 02, the project in one sentence |
| `compositions/explainer.html` | Slides 03-04, the white "how it works" chapter (edit its `CONFIG` block) |
| `compositions/s03-architecture.html` | Slide 05, the architecture diagram (boxes, cylinders, the `W` wires object, packets) |
| `compositions/s04-routes.html` | Slide 06, 51 replayed days and the metric that disagrees (data injected as `SERIES`) |
| `compositions/value.html` | Slide 07, the sale-day roster and ops-lead scene (data injected as `SHEET`) |
| `compositions/s05-results.html`, `s06-twist.html`, `s07-end.html` | Slides 08-10, results, what didn't win, end card (backdrop injected as `SALE`) |
| `assets/run_series.json` | Daily replayed on-time per plan and the slot-cap ablation, exported by `tools/export_series.py` |
| `assets/sale_day_roster.json` | Sale day 2025-11-28, all 12 hubs, re-planned and replayed by `tools/export_roster.py` |
| `audit/facts.yaml` | Every figure (F-xx), assumption (A-xx), slide text and check expression used by the report |
| `tools/build_audit_report.py` | Builds the slide stills, re-checks every figure against the repo outputs, writes MD, DOCX and PDF |
| `tools/export_series.py` | Reads `results/hub_day.csv` and `results/plan_summary.csv` into the slide 06 series |
| `tools/export_roster.py` | Re-runs the repo's own cleaning, forecast, MIP and replay for the sale day (config `mip_t99_d85`) |
| `tools/inject_sheet.py` | Inlines those JSON files into the compositions (renders cannot fetch files) |
| `package.json`, `hyperframes.json`, `meta.json` | HyperFrames project files (CLI pinned to 0.8.156) |

The repo writes no plots, so slide 06 draws its lines from the CSV values above rather than from
an image. Fonts (Inter, JetBrains Mono, Montserrat) are referenced by name and embedded by the
HyperFrames renderer; all three are SIL Open Font License.

## Requirements

- Node 22 and `npx` (HyperFrames 0.8.156 is fetched by `npx`), plus Chrome/Chromium for rendering.
- `ffmpeg` for compressing the video and extracting slide stills.
- LibreOffice (`soffice`) to convert the report to PDF.
- The repo's own environment (`uv sync`) for `make run`, `export_series.py` and `export_roster.py`.
- For `build_audit_report.py` and `inject_sheet.py` (any Python 3.11+, not added to the repo's
  `pyproject.toml`): `pip install pyyaml pandas python-docx pillow numpy matplotlib`.

## Rebuild the numbers, the video and the report

```bash
# 1. repo outputs (about 3 minutes)
make run

# 2. inputs for the video
.venv/bin/python docs/demo/source/tools/export_series.py docs/demo/source/assets/run_series.json
.venv/bin/python docs/demo/source/tools/export_roster.py docs/demo/source/assets/sale_day_roster.json
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/run_series.json \
  docs/demo/source/compositions/s04-routes.html --marker SERIES
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/sale_day_roster.json \
  docs/demo/source/compositions/value.html --marker SHEET --rows A1,A2,A4,B1,B2,C1 --animate A1,B2,C1
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/sale_day_roster.json \
  docs/demo/source/compositions/s07-end.html --marker SALE

# 3. render (86.2 s, 1920x1080, 30 fps, silent) and compress
cd docs/demo/source
npx --yes hyperframes@0.8.156 check
npx --yes hyperframes@0.8.156 render -f 30 -q looks -o renders/peak-capacity-planner-master.mp4
cd -
ffmpeg -y -i docs/demo/source/renders/peak-capacity-planner-master.mp4 -c:v libx264 -preset slow -crf 25 \
  -pix_fmt yuv420p -movflags +faststart -an docs/demo/demo.mp4

# 4. audit report: slide stills, automated checks, AUDIT_REPORT.md/.docx/.pdf
python3 docs/demo/source/tools/build_audit_report.py docs/demo/source/audit/facts.yaml
```

`build_audit_report.py` exits with code 1 if any figure on screen no longer matches the repo outputs.
CBC stops on a node limit, so a fresh `make run` normally reproduces `results/` exactly; check F-50
confirms that the re-planned sale day equals `results/hub_day.csv` hub by hub. The report lists which
figures and slides each input affects (change-impact matrix).

The `renders/` folder (about 15 MB) is not committed; step 3 recreates it.
