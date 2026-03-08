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

## Host on GitHub Pages

This repo includes a workflow at `.github/workflows/deploy-pages.yml` that:

1. runs `uv run mainz-chargers`
2. publishes `mainz_map.html` as `index.html`
3. also publishes `mainz_stats.json` and `mainz_stations.csv`

One-time repo setup:

1. Push this branch to GitHub.
2. In GitHub, open `Settings -> Pages`.
3. Under `Build and deployment`, set `Source` to `GitHub Actions`.
4. Run the `Deploy Mainz Map to GitHub Pages` workflow (or push to `main`/`master`).

After deployment, your map will be available at:

`https://<your-user-or-org>.github.io/<your-repo>/`
