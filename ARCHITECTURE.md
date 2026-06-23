# Architecture

## Overview

A Python CLI tool that searches for budget flights from a European departure airport within a given time range and budget. It scrapes budget airline websites, caches results locally for 1 week, and displays results as a table. A web interface is also planned to display results in the browser.

## CLI Usage

```bash
python flight_search.py [DEPARTURE_AIRPORT] [TIMERANGE] [BUDGET]
```

| Argument | Format | Examples | Default |
|----------|--------|---------|---------|
| `departure_airport` | IATA code (EU only) | `EIN`, `AMS`, `LHR` | `EIN` |
| `timerange` | `{n}d` / `{n}w` / `{n}m` | `3d`, `2w`, `1m` | `1m` |
| `budget` | Integer (euros) | `50`, `100` | `50` |

Max search range: 3 months.

## Goals & Non-Goals

**Goals**
- CLI interface: `python flight_search.py EIN 1m 50`
- Validate IATA codes (European airports only)
- Scrape budget airline websites for flight data
- Cache results in a local file (1 week TTL, timestamp in filename)
- Display results as a table: destination (IATA code, city, country), airline, departure time, arrival time, price
- Web interface to display flight results

**Non-Goals**
- Booking flights
- Non-European departure airports
- Searches beyond 3 months
- Real-time pricing accuracy

## High-Level Diagram
┌─────────┐   args    ┌──────────┐   cache hit   ┌───────────┐

│   CLI   │──────────▶│  Cache   │──────────────▶│  Display  │

│         │           │  Manager │               │   Table   │

└─────────┘           └──────────┘               └───────────┘

│ cache miss

▼

┌──────────┐

│ Scraper  │

│          │

└──────────┘

│

┌────────────────┼────────────────┐

▼                ▼                ▼

┌─────────┐     ┌─────────┐     ┌─────────┐

│ Ryanair │     │ easyJet │     │Wizz Air │

└─────────┘     └─────────┘     └─────────┘

▼                ▼                ▼

┌─────────┐     ┌─────────┐

│ Vueling │     │Norwegian│  ... Eurowings

└─────────┘     └─────────┘

## Components

### `cli.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Parse and validate CLI arguments |
| Inputs         | `DEPARTURE_AIRPORT` (IATA code, EU only), `TIMERANGE` (e.g. `3d`=3 days, `2w`=2 weeks, `1m`=1 month), `BUDGET` (euros) |
| Outputs        | Validated params passed to cache manager |
| Key files      | `cli.py` |
| External calls | None |

### `cache.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Read/write flight data cache, check TTL |
| Inputs         | Departure airport, date range |
| Outputs        | Cached flight list or cache miss signal |
| Key files      | `cache.py` |
| External calls | Local filesystem — cache files named `{airport}_{timestamp}.json` |

### `scraper.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Scrape budget airline sites for flights |
| Inputs         | Departure airport, date range |
| Outputs        | List of `{destination, airline, departure_time, arrival_time, price}` |
| Key files      | `scraper.py` |
| External calls | Ryanair, easyJet, Wizz Air, Vueling, Norwegian, Eurowings |

### `display.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Format and print results as a table |
| Inputs         | List of flight dicts |
| Outputs        | Printed table to stdout |
| Key files      | `display.py` |
| External calls | None |

### `web/` *(planned)*

| Field          | Value |
|----------------|-------|
| Responsibility | Web interface to display flight results |
| Inputs         | Flight data from cache or scraper |
| Outputs        | HTML page with searchable/filterable results table |
| Key files      | `web/app.py`, `web/templates/index.html` |
| External calls | None |

## Data Flow

1. User runs `python flight_search.py EIN 1m 50`
2. `cli.py` validates airport (EU only), parses time range, validates budget
3. `cache.py` checks for a cache file newer than 1 week for this airport
4. If cache hit → load flights from file
5. If cache miss → `scraper.py` fetches flights from all supported airlines, saves to cache
6. Filter flights by budget
7. `display.py` prints results as table

## Data Model
Flight:
destination_iata: str      # e.g. "BCN"
destination_city: str      # e.g. "Barcelona"
destination_country: str   # e.g. "Spain"
airline: str               # e.g. "Ryanair"
departure_time: datetime
arrival_time: datetime
price_eur: float

Cache file: `{airport}_{YYYYMMDD_HHMMSS}.json` — list of Flight objects

## Key Design Decisions

| # | Decision | Alternatives considered | Rationale |
|---|----------|------------------------|-----------|
| 1 | Scrape airline sites | Use a flight API (Skyscanner, Amadeus) | APIs require registration/payment; scraping is free |
| 2 | Cache as local JSON file | SQLite, Redis | Simplest approach for a CLI tool |
| 3 | 1 week cache TTL | No cache, daily cache | Balance between freshness and avoiding frequent scrapes |
| 4 | Support 6 airlines | All European budget airlines | Covers majority of European budget routes |
| 5 | Static lookup table for IATA to city/country mapping | External API | Simpler, no API dependency, EU airports list is finite |
| 6 | Scraping only, no airline APIs | Ryanair unofficial API | Free tier limits too restrictive for practical use |
| 7 | Web UI deferred until CLI is fully working | Build UI first | CLI is the top priority; UI is a nice-to-have |

## External Dependencies

| Name | Version | Purpose | Docs |
|------|---------|---------|------|
| requests | latest | HTTP requests for scraping | https://docs.python-requests.org |
| beautifulsoup4 | latest | HTML parsing | https://www.crummy.com/software/BeautifulSoup |
| rich | latest | Table display in terminal | https://rich.readthedocs.io |
| playwright | latest | JS-heavy page scraping | https://playwright.dev/python |
| iata-data | - | Static IATA airport lookup (bundled as JSON) | - |

## Constraints & Assumptions

- European airports only for departure
- Max search range is 3 months
- Prices may be stale up to 1 week due to caching
- Airline websites may change their HTML structure and break the scraper
- Some airlines may require JavaScript rendering (playwright used as fallback)

## Open Questions

- [ ] Web interface: simple HTML page, or a framework like Flask/FastAPI + React?
- [ ] Should the web UI replace the CLI or run alongside it?
- [ ] Real-time search in the browser or trigger from CLI and view results in browser?
- [ ] How to handle scraping failures gracefully — skip airline or abort?

## Supported Airlines

| Airline | Website | API available |
|---------|---------|---------------|
| Ryanair | ryanair.com | Unofficial API |
| easyJet | easyjet.com | No |
| Wizz Air | wizzair.com | No |
| Vueling | vueling.com | No |
| Norwegian | norwegian.com | No |
| Eurowings | eurowings.com | No |

## Non-Functional Requirements

| Concern        | Target / Constraint |
|----------------|---------------------|
| Latency        | Scraping may take 30-60s per airline |
| Cache TTL      | 1 week |
| Max search range | 3 months |
| Supported OS   | macOS, Linux |

## Decision Log (ADR summary)

| ADR | Decision | Status |
|-----|----------|--------|
| 001 | Use local JSON cache over database | Accepted |
| 002 | Scrape airlines directly over paid API | Accepted |
| 003 | Support 6 major European budget airlines | Accepted |
