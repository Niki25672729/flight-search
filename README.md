# ✈️ flight-search

A Python CLI tool that finds budget flights from a European airport within your time range and budget. Results are scraped from Ryanair, cached daily, and displayed as a colour-coded terminal table.

![Python](https://img.shields.io/badge/Python-3.12+-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Ryanair](https://img.shields.io/badge/Airline-Ryanair-orange)

---

## Demo

[![Open Dashboard](https://img.shields.io/badge/📊_Live_Dashboard-Open-blue?style=for-the-badge)](https://datastudio.google.com/s/rQv3xBI1Pr4)

```
> python src/flight_search.py Eindhoven Italy 2026-08-05...2026-08-12 40 price

2026-07-14 22:05:54 – INFO – Searching flights from EIN to AHO/AOI/BDS/BGY/BLQ/... between 2026-08-05 and 2026-08-12 within €40 budget...
2026-07-14 22:05:57 – INFO – GCS cache hit for ryanair-EIN: loaded bronze/flights/ryanair/202607/14/EIN_20260714.json
2026-07-14 22:05:57 – INFO – Found 13 flights matching criteria.

                Budget Flight Search Results
┌────────────────────────────┬─────────┬──────────────────┬────────┐
│ Destination                │ Airline │ Departure Time   │  Price │
├────────────────────────────┼─────────┼──────────────────┼────────┤
│ BLQ, Bologna, Italy        │ Ryanair │ 2026-08-09 19:30 │ €27.80 │
│ BLQ, Bologna, Italy        │ Ryanair │ 2026-08-07 18:25 │ €28.91 │
│ BGY, Milan Bergamo, Italy  │ Ryanair │ 2026-08-12 20:25 │ €35.07 │
│ PSA, Pisa, Italy           │ Ryanair │ 2026-08-12 19:35 │ €24.99 │
│ TSF, Venice Treviso, Italy │ Ryanair │ 2026-08-12 09:40 │ €25.99 │
└────────────────────────────┴─────────┴──────────────────┴────────┘
```

---

## Features

- **Simple CLI** — one command, five optional arguments
- **Flexible places** — departure and destination accept an airport IATA code, a city, or a whole country; partial names match when unambiguous (`Kingdom` → United Kingdom), typos get "did you mean" suggestions
- **Smart caching** — scrapes once per day, checking GCS first then falling back to a local cache; instant results on repeat same-day runs
- **Date range + destination + budget filtering** — exact travel dates, optional destination, and a price cap; sortable by date or price
- **Colour-coded table** — easy to scan in the terminal via `rich`
- **Unknown airport discovery** — new airports found during scraping are logged for review

---

## Data Source & Responsible Use

This tool uses Ryanair's internal "cheapest fares" API (`/farfnd/v4/oneWayFares`, via the [`ryanair-py`](https://github.com/cohaolain/ryanair-py) library), the same undocumented endpoint their own website calls in the browser to power fare search. There is no official public Ryanair API, so this is disclosed here upfront rather than left for someone to discover on their own:

- **Undocumented, unofficial endpoint.** No SLA, no versioning guarantees, and no notice if the response shape changes. This project is built and shared for educational / personal-portfolio purposes, not commercial use.
- **Rate-limited by design.** `ryanair-py` retries transient failures with exponential backoff internally, and this project adds its own pacing delay between requests, staying well under anything resembling aggressive polling; every response is cached locally (see [Caching](#caching)) so repeat runs don't re-hit the API at all.
- **`robots.txt`-aware.** Scraping behaviour respects `ryanair.com`'s `robots.txt` directives where they apply to the endpoints used.
- **Not affiliated with Ryanair.** All trademarks belong to their respective owners; this is an independent, unofficial tool.

If you fork or run this yourself, please keep request frequency low and cache aggressively — both to stay respectful of Ryanair's infrastructure and to keep the tool from getting your IP throttled or blocked.

---

## Supported Airlines

| Airline   | Status    |
|-----------|-----------|
| Ryanair   | ✅ v1      |
| easyJet   | 🔜 planned |
| Wizz Air  | 🔜 planned |
| Vueling   | 🔜 planned |
| Norwegian | 🔜 planned |
| Eurowings | 🔜 planned |

---

## Installation

**1. Clone the repo**
```bash
git clone https://github.com/Niki25672729/flight-search.git
cd flight-search
```

**2. Install dependencies**
```bash
uv sync --extra dev
```

> Requires [uv](https://docs.astral.sh/uv/). Install with `pip install uv` if needed.

---

## Setup

No API key or account needed — the tool queries Ryanair's public fare-search endpoint directly via the [`ryanair-py`](https://github.com/cohaolain/ryanair-py) library.

---

## Usage

```bash
python src/flight_search.py [departure] [destination] [timerange] [budget] [sort]
```

| Argument      | Format                                                  | Examples                            | Default         |
|---------------|---------------------------------------------------------|-------------------------------------|-----------------|
| `departure`   | IATA code, city, or country (EU only)                   | `EIN`, `Eindhoven`, `Netherlands`   | `EIN`           |
| `destination` | IATA code, city, or country; `none` = any destination   | `BCN`, `Barcelona`, `Spain`, `none` | `none`          |
| `timerange`   | `yyyy-mm-dd...yyyy-mm-dd`, or a single `yyyy-mm-dd` day | `2026-10-01...2026-10-30`           | today + 30 days |
| `budget`      | Integer (euros)                                         | `50`, `100`                         | `50`            |
| `sort`        | `date` or `price` (ordering within each destination)    | `price`                             | `date`          |

Departure and destination must be the same kind: IATA pairs with IATA, names (city/country) pair with names — `EIN Barcelona` is rejected, `Eindhoven Barcelona` works. A city or country covers **all** of its airports (e.g. `Paris` → CDG + ORY), and the results table gains a `Departure` column so rows stay traceable. Partial names resolve when they match exactly one place; typos fail with a "did you mean" suggestion rather than a silent guess.

**Examples:**
```bash
# Flights from Eindhoven to anywhere in the next 30 days under €50 (defaults)
python src/flight_search.py

# Amsterdam → Barcelona in the first two October weeks, under €75
python src/flight_search.py AMS BCN 2026-10-01...2026-10-14 75

# Eindhoven → anywhere in Italy on one specific day, cheapest first
python src/flight_search.py Eindhoven Italy 2026-08-12 40 price

# Partial country name — resolves to United Kingdom (all its airports)
python src/flight_search.py Eindhoven Kingdom 2026-10-01...2026-10-30 60
```

---

## How It Works

1. CLI arguments are parsed and validated (`cli.py`) — departure/destination resolve to airport codes (a city or country covers all of its airports)
2. Cache is checked for each departure airport (`cache.py`) — GCS first, falling back to the local cache if GCS is unreachable
3. **Cache hit** → load flights instantly from today's cached data
4. **Cache miss** → scrape Ryanair for the cheapest fare per destination, per day, across the next 3 months + 1 week buffer (`scraper.py`), save to cache
5. Filter by date range, destination, and budget; sort by date or price (`flight_search.py`)
6. Display results as a colour-coded terminal table (`display.py`), with a `Departure` column when several origin airports were searched

---

## Project Structure

```
/
├── src/
│   ├── __init__.py           # Package marker — do not modify
│   ├── flight_search.py      # Entry point
│   ├── cli.py                # Argument parsing and validation
│   ├── config.py             # Constants and file paths
│   ├── models.py             # Flight dataclass
│   ├── utils.py              # Airport data helpers
│   ├── cache.py              # Local JSON cache
│   ├── scraper.py            # Ryanair scraper
│   ├── display.py            # Terminal table output
│   └── eu_airports.json      # Static IATA lookup table
├── tests/
│   ├── conftest.py
│   ├── test_cli.py
│   ├── test_cache.py
│   ├── test_scraper.py
│   ├── test_display.py
│   └── test_flight_search.py
├── ARCHITECTURE.md           # v1 system design and decisions
├── ARCHITECTURE_DASHBOARD.md # v2 pipeline design and decisions
├── CLAUDE.md                 # AI agent guide
├── pyproject.toml
└── uv.lock                   # Pinned dependency versions — do not edit manually
```

---

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/

# Format
uv run ruff format src/

# Type-check
uv run mypy src/
```

---

## Caching

- The CLI checks Google Cloud Storage first (shared with the v2 pipeline below); if GCS is unreachable, it falls back automatically to a local cache at `cache/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json` — see [ARCHITECTURE_DASHBOARD.md](./ARCHITECTURE_DASHBOARD.md)'s "Shared GCS Cache Convention"
- Cache TTL is **1 day** — scraping runs daily, so a fresh scrape is triggered once per calendar day
- Local cache files are gitignored

---

## Roadmap: v2 — Data Engineering Pipeline

v1 is the CLI tool documented above, and it **stays fully functional** — v2 is additive, not a replacement. The same repo grows a pipeline and dashboard layer on top of the existing scraper, chosen to run entirely on free tiers / free trial credit:

```
src/                    # existing v1 CLI + shared core lib (scraper, cache, models) — unchanged, imported by the pipeline
infrastructure/
  terraform/            # IaC — GCS bucket + service account (impersonation, no downloaded keys)
  docker/               # Dockerfiles for each per-task container
  airflow/              # Orchestration — Airflow DAG, runs locally via Docker Compose
pipeline/
  ingestion/            # scheduled scrape → bronze (raw JSON in GCS)
  processing/           # PySpark jobs: bronze → silver (clean, dedupe, type)
  transform/            # dbt Core project: silver → gold (star schema, tests, docs)
dashboards/
  looker/               # Looker Studio dashboard on top of gold tables, connected directly to BigQuery
```

| Stage          | Tool                          | Free tier / trial                                                                                  |
|----------------|-------------------------------|----------------------------------------------------------------------------------------------------|
| Infrastructure | Terraform (`google` provider) | Provisions the GCS bucket + service account — free (IaC tooling, not a hosted resource)            |
| Ingestion      | Existing scraper, scheduled   | Triggered by the Airflow DAG                                                                       |
| Landing        | Google Cloud Storage          | 5 GB free tier                                                                                     |
| Processing     | PySpark (local `local[*]`)    | Runs as a local Spark session, in its own container — no external cluster                          |
| Warehouse      | BigQuery                      | 10 GB storage + 1 TB queries/month free                                                            |
| Transform      | dbt Core                      | Open source, free                                                                                  |
| Dashboard      | Looker Studio                 | Free; connects directly to BigQuery, sharing via public or restricted link                         |
| Orchestration  | Apache Airflow                | Self-hosted via Docker Compose — free forever                                                      |

See [ARCHITECTURE_DASHBOARD.md](./ARCHITECTURE_DASHBOARD.md) for the full v2 design, component responsibilities, and the trade-offs behind each choice — kept as its own document, separate from `ARCHITECTURE.md` (v1), so the two systems stay easy to reason about independently.

Status: 🔜 in progress.

---

## Future Ideas (Beyond v2)

- **Price forecasting** — predict future fare trends using historical price data once the v2 warehouse (gold tables) is in place

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change. Please run `ruff`, `mypy`, and `pytest` before submitting.

---

## License

[MIT](LICENSE) — any derivative work must also be open source under the same license.
