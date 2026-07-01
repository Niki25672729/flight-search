# Architecture

## Overview

A Python CLI tool that searches for budget flights from a European departure airport within a given time range and budget. It scrapes budget airline websites, caches results locally for 1 week, and displays results as a table. A web interface is also planned to display results in the browser.

## CLI Usage

```bash
python flight_search.py [departure_airport] [timerange] [budget]
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
- Web interface to display flight results (deferred)
- Scrape Ryanair flights for v1; other airlines planned for future versions

**Non-Goals**
- Booking flights
- Non-European departure airports
- Searches beyond 3 months
- Real-time pricing accuracy

## High-Level Diagram

```text
         ┌──────────────────┐   cache hit   ┌───────────────┐
User────▶│   Entry Point    │──────────────▶│    Display    │
         └──────────────────┘               └───────────────┘
                 │
        ┌────────┴─────────┐
        ▼                  ▼ cache miss
  ┌───────────┐      ┌───────────┐
  │   Cache   │      │  Scraper  │
  │  Manager  │      │           │
  └───────────┘      └───────────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
        ┌─────────┐   ┌─────────┐   ┌─────────┐
        │ Ryanair │   │ easyJet │   │Wizz Air │
        └─────────┘   └─────────┘   └─────────┘
        ┌─────────┐   ┌─────────┐   ┌─────────┐
        │ Vueling │   │Norwegian│   │Eurowings│
        └─────────┘   └─────────┘   └─────────┘
```

## Components

### `cli.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Parse and validate CLI arguments |
| Inputs         | `departure_airport` (IATA code, EU only)<br>`timerange` (e.g. `3d`=3 days, `2w`=2 weeks, `1m`=1 month)<br>`budget` (euros) |
| Outputs        | Validated params passed to `flight_search.py` |
| Key files      | `cli.py` |
| External calls | None |

### `models.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Shared data model definitions |
| Inputs         | None |
| Outputs        | `Flight` dataclass imported by cache, scraper, display |
| Key files      | `models.py` |
| External calls | None |

### `cache.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Read/write flight data cache, check TTL |
| Inputs         | Departure airport (IATA code) |
| Outputs        | List of Flight objects on cache hit, None on cache miss or expiry |
| Key files      | `cache.py` |
| External calls | Local filesystem — cache files named `{airport}_{YYYYMMDD}.json` |

### `scraper.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Scrape budget airline sites for all flights from departure airport for next 3 months + 1 week buffer |
| Inputs         | Departure airport (IATA code) |
| Outputs        | List of Flight objects |
| Key files      | `scraper.py` |
| External calls | Ryanair, easyJet, Wizz Air, Vueling, Norwegian, Eurowings |

### `display.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Format and print results as a table |
| Inputs         | List of Flight objects |
| Outputs        | Printed table to stdout |
| Key files      | `display.py` |
| External calls | None |

### `flight_search.py`

| Field          | Value |
|----------------|-------|
| Responsibility | Orchestrate CLI, cache, scraper and display |
| Inputs         | Validated params from `cli.py` |
| Outputs        | Filtered flight results passed to `display.py` |
| Key files      | `flight_search.py` |
| External calls | `cache.py`, `scraper.py`, `display.py` |

### `web/` *(deferred)*

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
3. `flight_search.py` calls `cache.py` with departure airport
4. If cache hit → load flights from cache file as list of Flight objects
5. If cache miss or expired → `flight_search.py` calls `scraper.py` with departure airport
6. Scraper fetches all flights for next 3 months + 1 week buffer, returns list of Flight objects
7. `flight_search.py` calls `cache.py` write method to save scraped results
8. `flight_search.py` filters flights by time range and budget
9. `display.py` prints filtered results as table

## Data Model

Flight:
```
destination_iata: str        # e.g. "BCN"
destination_city: str        # e.g. "Barcelona"
destination_country: str     # e.g. "Spain"
airline: str                 # e.g. "Ryanair"
departure_time: datetime
arrival_time: datetime | None  # Not available for all airlines
price_eur: float
```

Cache file: `cache/{airport}_{YYYYMMDD}.json` — list of Flight objects

## Unknown Airport Discovery

When `scraper.py` encounters a destination IATA code not in `eu_airports.json`, it logs the new airport (city and country from the airline's response) to `src/unknown_airports.json`. This file should be periodically reviewed and merged into `eu_airports.json`.

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
| 8 | Scrape 3 months + 1 week buffer upfront | Scrape per query | Reduces scraping frequency; budget/timerange applied as filters |
| 9 | `flight_search.py` orchestrates all modules | Merge logic into cache.py | Clear separation of concerns |
| 10 | Cache filename uses YYYYMMDD only | Full timestamp | Date precision sufficient for 1 week TTL |
| 11 | `Flight` dataclass in `models.py` | Raw dicts | Type safety and shared definition across modules |
| 12 | `arrival_time` is optional (None) | Require arrival time | Not all airlines provide arrival time in scrape response |

## External Dependencies

| Name | Version | Purpose | Docs |
|------|---------|---------|------|
| ryanair-py | 3.0.0 | Ryanair flight data via unofficial API | https://github.com/cohaolain/ryanair-py |
| requests | latest | HTTP requests for scraping | https://docs.python-requests.org |
| beautifulsoup4 | latest | HTML parsing | https://www.crummy.com/software/BeautifulSoup |
| rich | latest | Table display in terminal | https://rich.readthedocs.io |
| playwright | latest | JS-heavy page scraping for future airlines | https://playwright.dev/python |

## Constraints & Assumptions

- European airports only for departure
- Max search range is 3 months
- Prices may be stale up to 1 week due to caching
- Airline websites may change their HTML structure and break the scraper
- Budget and time range are filter parameters applied after loading from cache, not scrape parameters
- `arrival_time` is None for airlines that do not provide it in their scrape response

## Open Questions

- [ ] Web interface: simple HTML page, or a framework like Flask/FastAPI + React?
- [ ] Should the web UI replace the CLI or run alongside it?
- [ ] Real-time search in the browser or trigger from CLI and view results in browser?
- [ ] How to handle scraping failures gracefully — skip airline or abort?

## Supported Airlines

| Airline | Website | Method | Status |
|---------|---------|--------|--------|
| Ryanair | ryanair.com | ryanair-py library | ✅ v1 |
| easyJet | easyjet.com | Scraping | 🔜 planned |
| Wizz Air | wizzair.com | Scraping | 🔜 planned |
| Vueling | vueling.com | Scraping | 🔜 planned |
| Norwegian | norwegian.com | Scraping | 🔜 planned |
| Eurowings | eurowings.com | Scraping | 🔜 planned |

## Non-Functional Requirements

| Concern          | Target / Constraint |
|------------------|---------------------|
| Latency          | Scraping may take 30-60s per airline |
| Cache TTL        | 1 week |
| Max search range | 3 months |
| Supported OS     | macOS, Linux |

## Decision Log (ADR summary)

| ADR | Decision | Status |
|-----|----------|--------|
| 001 | Use local JSON cache over database | Accepted |
| 002 | Scrape airlines directly, no paid or unofficial APIs | Accepted |
| 003 | Support 6 major European budget airlines | Accepted |
| 004 | `flight_search.py` orchestrates all modules | Accepted |
| 005 | Scrape 3 months + 1 week buffer, filter at display time | Accepted |
| 006 | `Flight` dataclass defined in `models.py`, shared across modules | Accepted |
| 007 | `arrival_time` is optional — not all airlines provide it | Accepted |
