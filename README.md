# ✈️ flight-search

A Python CLI tool that finds budget flights from a European airport within your time range and budget. Results are scraped from Ryanair, cached daily, and displayed as a colour-coded terminal table.

![Python](https://img.shields.io/badge/Python-3.12+-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Ryanair](https://img.shields.io/badge/Airline-Ryanair-orange)

---

## Demo

[![Open Dashboard](https://img.shields.io/badge/📊_Live_Dashboard-Open-blue?style=for-the-badge)](https://datastudio.google.com/s/rQv3xBI1Pr4)

```
> python src/flight_search.py EIN 3m 50

2026-07-03 11:06:49 – INFO – Searching flights from EIN within 90 days and €50 budget...
2026-07-03 11:06:49 – INFO – Cache hit for ryanair-EIN: Loaded EIN_20260703.json
2026-07-03 11:06:49 – INFO – Found 31 flights matching criteria.

                  Budget Flight Search Results
┌──────────────────────────────┬─────────┬──────────────────┬────────┐
│ Destination                  │ Airline │ Departure Time   │ Price  │
├──────────────────────────────┼─────────┼──────────────────┼────────┤
│ TIA, Tirana, Albania         │ Ryanair │ 2026-08-21 12:50 │ €19.99 │
│ VIE, Vienna, Austria         │ Ryanair │ 2026-09-12 20:45 │ €23.99 │
│ SOF, Sofia, Bulgaria         │ Ryanair │ 2026-09-16 15:40 │ €16.99 │
│ ZAG, Zagreb, Croatia         │ Ryanair │ 2026-08-17 08:45 │ €19.99 │
│ SKG, Thessaloniki, Greece    │ Ryanair │ 2026-08-23 13:30 │ €38.89 │
└──────────────────────────────┴─────────┴──────────────────┴────────┘
```

---

## Features

- **Simple CLI** — one command, three optional arguments
- **Smart caching** — scrapes once per day, checking GCS first then falling back to a local cache; instant results on repeat same-day runs
- **Budget + timerange filtering** — only shows flights that fit your constraints, sorted by price
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
python src/flight_search.py [departure_airport] [timerange] [budget]
```

| Argument            | Format                   | Examples            | Default |
|---------------------|--------------------------|---------------------|---------|
| `departure_airport` | IATA code (EU only)      | `EIN`, `AMS`, `LHR` | `EIN`   |
| `timerange`         | `{n}d` / `{n}w` / `{n}m` | `3d`, `2w`, `1m`    | `1m`    |
| `budget`            | Integer (euros)          | `50`, `100`         | `50`    |

**Examples:**
```bash
# Flights from Eindhoven in the next month under €50 (defaults)
python src/flight_search.py

# Flights from Amsterdam in the next 2 weeks under €75
python src/flight_search.py AMS 2w 75

# Flights from London Heathrow in the next 3 months under €100
python src/flight_search.py LHR 3m 100
```

---

## How It Works

1. CLI arguments are parsed and validated (`cli.py`)
2. Cache is checked for this departure airport (`cache.py`) — GCS first, falling back to the local cache if GCS is unreachable
3. **Cache hit** → load flights instantly from today's cached data
4. **Cache miss** → scrape Ryanair for the cheapest fare per destination, per day, across the next 3 months + 1 week buffer (`scraper.py`), save to cache
5. Filter by timerange and budget, sort by price (`flight_search.py`)
6. Display results as a colour-coded terminal table (`display.py`)

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
