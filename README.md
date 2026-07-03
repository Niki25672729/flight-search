# ✈️ flight-search

A Python CLI tool that finds budget flights from a European airport within your time range and budget. Results are scraped from Ryanair, cached locally for 1 week, and displayed as a colour-coded terminal table.

![Python](https://img.shields.io/badge/Python-3.12+-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Ryanair](https://img.shields.io/badge/Airline-Ryanair-orange)

---

## Demo

```
> python src/flight_search.py EIN 3m 50

2026-07-03 11:06:49 – INFO – Searching flights from EIN within 90 days and €50 budget...
2026-07-03 11:06:49 – INFO – Cache hit for EIN: Loaded EIN_20260703.json
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
- **Smart caching** — scrapes once, caches for 1 week; instant results on repeat runs
- **Budget + timerange filtering** — only shows flights that fit your constraints, sorted by price
- **Colour-coded table** — easy to scan in the terminal via `rich`
- **Unknown airport discovery** — new airports found during scraping are logged for review

---

## Supported Airlines

| Airline   | Status     |
|-----------|------------|
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

**3. Get a free Gemini API key**

Not required — this tool does not use an AI API. Skip this step.

---

## Setup

Get a free Ryanair session — no API key needed. The tool automatically fetches a session cookie on first run.

> ⚠️ First run may take up to 10 minutes while fetching the Ryanair session cookie. Subsequent runs within 1 hour use the cached cookie and are instant.

---

## Usage

```bash
python src/flight_search.py [departure_airport] [timerange] [budget]
```

| Argument            | Format                     | Examples            | Default |
|---------------------|----------------------------|---------------------|---------|
| `departure_airport` | IATA code (EU only)        | `EIN`, `AMS`, `LHR` | `EIN`   |
| `timerange`         | `{n}d` / `{n}w` / `{n}m`  | `3d`, `2w`, `1m`    | `1m`    |
| `budget`            | Integer (euros)            | `50`, `100`         | `50`    |

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
2. Local cache is checked for this departure airport (`cache.py`)
3. **Cache hit** → load flights instantly from `cache/{airport}_{YYYYMMDD}.json`
4. **Cache miss** → scrape Ryanair for all flights in the next 3 months + 1 week buffer (`scraper.py`), save to cache
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
├── ARCHITECTURE.md           # System design and decisions
├── AGENTS.md                 # AI agent guide
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

- Flight data is cached in `cache/{airport}_{YYYYMMDD}.json`
- Cache TTL is **1 week** — after that, a fresh scrape is triggered automatically
- Ryanair session cookies are cached in `src/.ryanair_cookies.json` for **1 hour**
- Both cache files are gitignored

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change. Please run `ruff`, `mypy`, and `pytest` before submitting.

---

## License

[MIT](LICENSE) — any derivative work must also be open source under the same license.
