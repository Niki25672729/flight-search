# AGENTS.md

<!-- README for AI coding agents. Keep this file concise — every line is loaded into
     context on every session. Aim for <150 lines. Update it in the same PR/commit
     that introduces or changes a convention. -->

## Project Overview

A Python CLI tool that searches for budget flights from a European departure airport within a given time range and budget. It scrapes budget airline websites, caches results locally for 1 week, and displays results as a table.

Entry point: `python src/flight_search.py [departure_airport] [timerange] [budget]`

See ARCHITECTURE.md for full design decisions.

## Tech Stack

| Layer         | Technology     | Version |
|---------------|----------------|---------|
| Language      | Python         | 3.12+   |
| HTTP client   | ryanair-py     | 3.0.0   |
| HTML parser   | beautifulsoup4 | latest  |
| HTTP          | requests       | latest  |
| Table display | rich           | latest  |
| Test runner   | pytest         | latest  |
| Linter        | ruff           | latest  |
| Formatter     | ruff format    | latest  |
| Env manager   | uv             | latest  |

## Setup & Commands

```bash
# Install dependencies (including dev tools)
uv sync --extra dev

# Run the CLI
uv run python src/flight_search.py EIN 1m 50

# Run the full test suite
uv run pytest

# Run a single test file
uv run pytest tests/test_scraper.py -v

# Lint
uv run ruff check src/

# Format
uv run ruff format src/

# Type-check
uv run mypy src/
```

## Project Layout

```
/
├── src/
│   ├── __init__.py             # Package marker — do not modify
│   ├── flight_search.py        # Entry point — orchestrates all modules
│   ├── cli.py                  # Argument parsing and validation
│   ├── models.py               # Shared data model — Flight dataclass
│   ├── cache.py                # Read/write local JSON cache
│   ├── scraper.py              # Scrape budget airline sites
│   ├── display.py              # Format and print results as table
│   ├── eu_airports.json        # Static IATA to city/country lookup
│   └── unknown_airports.json   # Auto-generated — unknown IATA codes discovered during scraping
├── tests/                      # pytest tests — mirrors src/ structure
│   ├── test_cli.py
│   ├── test_cache.py
│   ├── test_scraper.py
│   └── test_display.py
├── cache/                      # Auto-generated cache files (gitignored)
├── ARCHITECTURE.md
├── AGENTS.md
└── pyproject.toml
```

Do not edit files under `dist/`, `build/`, or `generated/` — they are auto-generated.

## Data Model

All modules import `Flight` from `models.py`. Do not use raw dicts for flight data.

```python
@dataclass
class Flight:
    destination_iata: str
    destination_city: str
    destination_country: str
    airline: str
    departure_time: datetime
    arrival_time: datetime | None  # Not available for all airlines
    price_eur: float
```

Cache file: `cache/{airport}_{YYYYMMDD}.json` — list of Flight objects serialized as JSON.

## Security

- Never read, log, or commit secrets, API keys, or credentials.
- Never make real network requests in tests — mock all HTTP calls.

## Agent Behaviour

- Confirm scope before writing code when the task is ambiguous.
- Make the smallest change that satisfies acceptance criteria.
- Do not introduce new dependencies without asking first.
- Do NOT use playwright, selenium, or any browser automation — use HTTP libraries only.
- Use pytest only — never use unittest.
- Use type hints on all functions.
- Add docstrings on all public methods.
- Always use the `Flight` dataclass from `models.py` — never use raw dicts for flight data.
- Handle scraping failures gracefully — log the error and return empty list, never raise.
- Summarise what changed and why at the end of each session.
