# CLAUDE.md

<!-- README for AI coding agents. Keep this file concise — every line is loaded into
     context on every session. Aim for <150 lines. Update it in the same PR/commit
     that introduces or changes a convention. -->

## Project Overview

A Python CLI tool that searches for budget flights from a European departure place (airport, city, or country) to an optional destination within a given date range and budget. It scrapes budget airline websites, caches results locally for 1 day (daily scraping), and displays results as a table.

Entry point: `python src/flight_search.py [departure] [destination] [timerange] [budget] [sort]`

See ARCHITECTURE.md for full design decisions.

## Tech Stack

| Layer          | Technology           | Version                             |
|----------------|----------------------|-------------------------------------|
| Language       | Python               | 3.12+                               |
| Ryanair client | ryanair-py           | 3.0.0                               |
| HTTP           | requests             | latest (transitive, via ryanair-py) |
| Table display  | rich                 | latest                              |
| Cloud cache    | google-cloud-storage | `cache.py` checks GCS first, falling back to the local file cache if GCS is unreachable — see ARCHITECTURE.md/ARCHITECTURE_DASHBOARD.md |
| Test runner    | pytest               | latest                              |
| Linter         | ruff                 | latest                              |
| Formatter      | ruff format          | latest                              |
| Env manager    | uv                   | latest                              |

**Future (not yet active):**

| Layer           | Technology     | Notes                                |
|-----------------|----------------|--------------------------------------|
| HTML parser     | beautifulsoup4 | For scraping easyJet, Wizz Air, etc. |
| Browser scraper | playwright     | For JS-heavy airline sites. Tried and removed for Ryanair itself (2026-07-07) — the endpoint it was working around (`booking/v4/availability`) isn't used anymore; ryanair-py's plain-`requests` session works fine for the endpoint actually used (`farfnd/v4/oneWayFares`) |

**v2 pipeline (planned, see ARCHITECTURE_DASHBOARD.md — does not affect v1 CLI):**

| Layer             | Technology                    | Notes                                                                               |
|-------------------|-------------------------------|-------------------------------------------------------------------------------------|
| IaC               | Terraform (`google` provider) | Provisions the GCS bucket + service account, see `infrastructure/terraform/`        |
| Processing        | PySpark (local `local[*]`)    | No external cluster; runs in its own container                                      |
| Landing/warehouse | GCS, BigQuery                 | GCP free tier                                                                       |
| Transform         | dbt Core (dbt-bigquery)       | Not dbt Cloud                                                                       |
| Orchestration     | Apache Airflow                | Local via Docker Compose                                                            |
| Dashboard         | Looker Studio                 | Connects directly to BigQuery, not Power BI/Tableau                                 |

## Setup & Commands

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Run the CLI
uv run python src/flight_search.py EIN BCN 2026-10-01...2026-10-30 50 price

# Run the full test suite
uv run pytest

# Run a single test file
uv run pytest tests/test_scraper.py -v

# Lint
uv run ruff check src/ pipeline/

# Format
uv run ruff format src/ pipeline/

# Type-check
uv run mypy src/ pipeline/
```

## Project Layout

```
/
├── src/
│   ├── __init__.py                 # Package marker — do not modify
│   ├── flight_search.py            # Entry point — orchestrates all modules
│   ├── cli.py                      # Argument parsing and validation
│   ├── config.py                   # All constants (cache, scraping, CLI, paths)
│   ├── models.py                   # Shared data model — Flight dataclass
│   ├── utils.py                    # Shared helpers — airport data loading
│   ├── cache.py                    # Read/write local JSON cache
│   ├── scraper.py                  # Scrape budget airline sites
│   ├── display.py                  # Format and print results as table
│   ├── eu_airports.json            # Static IATA to city/country lookup
│   ├── ignored_airports.json       # Static IATA that ignored during flight search
│   ├── ambiguous_airports.json     # Auto-generated — ambiguous IATA codes discovered during scraping
│   └── unknown_airports.json       # Auto-generated — unknown IATA codes discovered during scraping
├── tests/
│   ├── conftest.py                 # Shared fixtures, constants and helpers
│   ├── test_cli.py
│   ├── test_cache.py
│   ├── test_scraper.py
│   ├── test_display.py
│   └── test_flight_search.py
├── cache/                          # Auto-generated cache files (gitignored)
├── infrastructure/
│   ├── terraform/                  # IaC — GCS bucket + service account (impersonation, no downloaded keys)
│   ├── docker/                     # Dockerfiles for each per-task container
│   └── airflow/                    # Orchestration — Airflow DAG, runs locally via Docker Compose
├── pipeline/                       # v2, planned — see ARCHITECTURE_DASHBOARD.md
│   ├── ingestion/                  # Calls src/scraper.py, writes to GCS bronze
│   ├── processing/                 # PySpark: bronze → silver
│   └── transform/                  # dbt Core project: silver → gold
├── dashboards/
│   └── looker/                     # v2, planned — Looker Studio dashboard, connected directly to BigQuery
├── ARCHITECTURE.md                 # v1 design
├── ARCHITECTURE_DASHBOARD.md       # v2 design
├── CLAUDE.md
└── pyproject.toml
```

`pipeline/` and `dashboards/` are v2 and do not exist yet — build them additively. `src/` is v1 and must keep working standalone; `pipeline/ingestion/` should import from `src/`, never fork or copy its logic.

Auto-generated files — do not commit:
- `cache/` — flight data cache
- `src/ambiguous_airports.json` — airports discovered during scraping, review and merge into `eu_airports.json`
- `src/unknown_airports.json` — airports discovered during scraping, review and merge into `eu_airports.json` or `ambiguous_airports.json`

Do not edit files under `dist/`, `build/`, or `generated/` — they are auto-generated.

## Data Model

All modules import `Flight` from `models.py`. Do not use raw dicts for flight data.

```python
@dataclass
class Flight:
    origin_iata: str
    destination_iata: str
    destination_city: str
    destination_country: str
    airline: str
    flight_number: str | None  # e.g. "FR1926"; not always available
    departure_time: datetime
    arrival_time: datetime | None  # Not available for all airlines
    price_eur: float
    currency: str = "EUR"
    seats_left: int | None = None  # airline's reported seats/fares left; None = not reported by source
    scraped_at: datetime = field(default_factory=datetime.now)  # when THIS record was captured
```

Cache file: `cache/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json` — list of Flight objects serialized as JSON, one file per airline/day/origin (see `FLIGHT_CACHE_FILENAME`/`LOCAL_FLIGHT_CACHE_DIR` in `config.py`).

## Script Layout

Organize Python modules in this project top to bottom into commented sections — e.g. `Constants`, `Helpers`, `Public API`. Section names use Title Case (capitalize every leading word: `Main Function`, not `Main function`). Within a section, related functions can be grouped under a lighter subsection divider.

```python
# ---------------------------
# Section Name
# ---------------------------


# --- Subsection Name ---

def some_function(): ...
```

Always leave 2 blank lines before a function def, regardless of section/subsection — `ruff format` enforces this and will revert anything else.

## Security

- Never read, log, or commit secrets, API keys, or credentials.
- Never read, log, or commit `infrastructure/terraform/terraform.tfvars` or `*.tfstate*` — gitignored, but can carry project IDs/resource details.
- Never make real network requests in tests — mock all HTTP calls.
- When running an ad hoc `docker run` against a project image for verification (e.g. checking imports), always pass `--entrypoint <interpreter>` (e.g. `--entrypoint python`) rather than appending a command after the image name. Dockerfiles here use exec-form `ENTRYPOINT` (e.g. `infrastructure/docker/ingestion/Dockerfile` runs `run.py`); a trailing `docker run <image> python -c "..."` is appended as *arguments* to that entrypoint, not a replacement, and can trigger the real script (e.g. a live scrape run) instead of the intended one-off command.

## Agent Behaviour

- Confirm scope before writing code when the task is ambiguous.
- Make the smallest change that satisfies acceptance criteria.
- Do not introduce new dependencies without asking first.

- Use pytest only — never use unittest.
- Use type hints on all functions.
- Add docstrings on all public methods.
- Always use the `Flight` dataclass from `models.py` — never use raw dicts for flight data.
- Handle scraping failures gracefully — log the error and return empty list, never raise.
- When working in `pipeline/` or `dashboards/` (v2), never modify `src/` behaviour or its CLI output — v1 must keep working standalone. Import from `src/`, don't duplicate it.
- `cache.py` checks GCS first (falling back to the local file cache only if GCS itself is unreachable, not merely stale) — see ARCHITECTURE.md/ARCHITECTURE_DASHBOARD.md. Don't regress this to a local-only check.
- Summarise what changed and why at the end of each session.
