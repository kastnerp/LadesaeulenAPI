# Ladesaeulen API

Python CLI tool to generate an interactive EV charging map and extended city stats for **Mainz** from official German sources.

## Data sources

- Charging stations: Bundesnetzagentur Ladesaeulenregister (official CSV)
- City boundary: BKG `wfs_vg250` administrative municipality geometry

## Requirements

- [`uv`](https://docs.astral.sh/uv/) installed

## Run

```bash
uv run mainz-chargers
```

Optional flags:

```bash
uv run mainz-chargers --output-dir output
uv run mainz-chargers --include-non-operational
uv run mainz-chargers --csv-url "https://data.bundesnetzagentur.de/.../Ladesaeulenregister_BNetzA_2026-02-27.csv"
```

## Output files

The command writes files to `output/` by default:

- `mainz_map.html`: interactive map (red = fast charging, blue = normal charging)
- `mainz_stats.json`: extended aggregated stats
- `mainz_stations.csv`: Mainz-only filtered station list
- `ladesaeulenregister_<date>.csv`: downloaded raw source snapshot
