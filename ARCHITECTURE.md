# Architecture

## Overview

A Python CLI tool that searches for budget flights from a European departure airport within a given time range and budget. It scrapes budget airline websites, caches results daily, and displays results as a table. A web interface is also planned for a future version.

> This document covers v1 (the CLI). v2 — a data engineering pipeline and Looker Studio dashboard built on top of the same scraper — is documented separately in [ARCHITECTURE_DASHBOARD.md](./ARCHITECTURE_DASHBOARD.md). v1 stays fully functional; v2 is purely additive and lives in its own doc so the two systems don't get tangled together.

## CLI Usage

```bash
python flight_search.py [departure] [destination] [timerange] [budget] [sort]
```


| Argument      | Format                                                  | Examples                            | Default         |
|---------------|---------------------------------------------------------|-------------------------------------|-----------------|
| `departure`   | IATA code, city, or country (EU only)                   | `EIN`, `Eindhoven`, `Netherlands`   | `EIN`           |
| `destination` | IATA code, city, or country; `none` = any destination   | `BCN`, `Barcelona`, `Spain`, `none` | `none`          |
| `timerange`   | `yyyy-mm-dd...yyyy-mm-dd`, or a single `yyyy-mm-dd` day | `2026-10-01...2026-10-30`           | today + 30 days |
| `budget`      | Integer (euros)                                         | `50`, `100`                         | `50`            |
| `sort`        | `date` or `price` (ordering within each destination)    | `price`                             | `date`          |


Departure and destination must be the same kind: IATA with IATA, or name (city/country) with name; `none` is neutral. A city/country resolves to all of its airports (see decision #23). Name matching is exact first, then unique substring (`Kingdom` → United Kingdom); ambiguous or misspelled input fails with the candidate list or a "did you mean" suggestion — never a silent guess.

Max search range: 3 months.

## Goals & Non-Goals

**Goals**
- CLI interface: `python flight_search.py EIN BCN 2026-10-01...2026-10-30 50 price`
- Validate IATA codes (European airports only); resolve city/country names to their airports
- Scrape budget airline websites for flight data — every flight per day so v2 can do flight analysis beyond simple budget search
- Cache results (GCS-first, local fallback; 1 day TTL, timestamp in filename) — scraping runs daily
- Display results as a table: destination (IATA code, city, country), airline, departure time, arrival time, price
- Scrape Ryanair flights for v1; other airlines planned for future versions
- Web interface to display flight results (deferred)

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

### `flight_search.py`

| Field          | Value                                                      |
|----------------|------------------------------------------------------------|
| Responsibility | Entry point — orchestrates CLI, cache, scraper and display |
| Inputs         | Validated params from `cli.py`                             |
| Outputs        | Filtered flight results passed to `display.py`             |
| Key files      | `flight_search.py`                                         |
| External calls | `cache.py`, `scraper.py`, `display.py`                     |

### `cli.py`

| Field          | Value                                         |
|----------------|-----------------------------------------------|
| Responsibility | Parse and validate CLI arguments              |
| Inputs         | `departure` (IATA code, city, or country — EU only) `destination` (same kinds, or `none` = any; must be the same kind as departure — IATA with IATA, name with name) `timerange` (`yyyy-mm-dd...yyyy-mm-dd`, or a single `yyyy-mm-dd` = that day) `budget` (euros) `sort` (`date` or `price`) |
| Outputs        | Validated params passed to `flight_search.py` — places resolved to lists of airport codes |
| Key files      | `cli.py`                                      |
| External calls | None                                          |

### `config.py`

| Field          | Value                                                     |
|----------------|-----------------------------------------------------------|
| Responsibility | All constants — cache, scraping, CLI defaults, file paths |
| Inputs         | None                                                      |
| Outputs        | Constants imported by all modules                         |
| Key files      | `config.py`                                               |
| External calls | None                                                      |

### `models.py`

| Field          | Value                                                  |
|----------------|--------------------------------------------------------|
| Responsibility | Shared data model definitions                          |
| Inputs         | None                                                   |
| Outputs        | `Flight` dataclass imported by cache, scraper, display |
| Key files      | `models.py`                                            |
| External calls | None                                                   |

### `utils.py`

| Field          | Value                       |
|----------------|-----------------------------|
| Responsibility | Shared helpers — load `eu_airports.json`/`ignored_airports.json`, build the city/country name → airports lookup used by `cli.py`, classify destination codes, write `unknown_airports.json`/`ambiguous_airports.json` (see [Airport Data Reconciliation](#airport-data-reconciliation)) |
| Inputs         | None                        |
| Outputs        | Airport-related information |
| Key files      | `utils.py`                  |
| External calls | Local filesystem — `eu_airports.json`, `ignored_airports.json`, `unknown_airports.json`, `ambiguous_airports.json` |

### `cache.py`

| Field          | Value                                                                        |
|----------------|------------------------------------------------------------------------------|
| Responsibility | Checks GCS first for flight data cache (daily TTL, since scraping runs daily). On a miss, the caller scrapes and writes the result back to GCS. Falls back entirely to the local filesystem cache if GCS itself is unreachable (network/auth error — not just a normal miss); see ARCHITECTURE_DASHBOARD.md's "Shared GCS Cache Convention" |
| Inputs         | Departure airport (IATA code), airline, and today's date (`yyyymmdd` string) |
| Outputs        | List of `Flight` objects on cache hit, `None` on cache miss or expiry        |
| Key files      | `cache.py`                                                                   |
| External calls | Google Cloud Storage (primary — `bronze/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json`); local filesystem as fallback — `cache/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json` |

### `scraper.py`

| Field          | Value                                                                              |
|----------------|------------------------------------------------------------------------------------|
| Responsibility | Fetches the CHEAPEST one-way fare per destination, per day, from the origin across the next 3 months + 1 week buffer — one query per day (see decision #16) |
| Inputs         | Departure airport (IATA code)                                                      |
| Outputs        | List of `Flight` objects (cheapest fare only — no `seats_left`, no `arrival_time`) |
| Key files      | `scraper.py`                                                                       |
| External calls | Ryanair `farfnd/v4/oneWayFares` endpoint via the `ryanair-py` library, one call per day in the buffer; easyJet, Wizz Air, Vueling, Norwegian, Eurowings (planned) |

### `display.py`

| Field          | Value                                    |
|----------------|------------------------------------------|
| Responsibility | Format and print results as a rich table, in the caller's order (sorting lives in `flight_search.py`'s sort modes) |
| Inputs         | List of Flight objects; `show_origin` flag adding a `Departure` column when a search spans several origin airports |
| Outputs        | Printed table to stdout                  |
| Key files      | `display.py`                             |
| External calls | None                                     |

### `web/` *(deferred)*

| Field          | Value                                              |
|----------------|----------------------------------------------------|
| Responsibility | Web interface to display flight results            |
| Inputs         | Flight data from cache or scraper                  |
| Outputs        | HTML page with searchable/filterable results table |
| Key files      | `web/app.py`, `web/templates/index.html`           |
| External calls | None                                               |

## Data Flow

1. User runs `python src/flight_search.py Eindhoven Italy 2026-08-05...2026-08-12 40 price`
2. `cli.py` resolves departure and destination to airport codes (see "CLI Usage" for the matching rules), parses the date range, validates budget and sort mode
3. `flight_search.py` computes today's date once (`yyyymmdd` string) and, for each resolved departure airport, checks the cache for that airport/airline/date
4. If flight cache hit → load flights from GCS (or the local fallback file) as list of Flight objects
5. If flight cache miss or expired:
  1. `flight_search.py` calls `scraper.py` with departure airport
  2. `scraper.py` loops over each day in the next 3 months + 1 week buffer, calling `ryanair-py`'s `get_cheapest_flights(origin, day, day)` — one call per day, since a single call spanning a wider range only returns the single cheapest fare across that ENTIRE range, not a per-day breakdown
  3. Each day's results cover every destination Ryanair flies that day (no separate route-discovery call needed); each destination code is classified once (see "Airport Data Reconciliation") and cached in-memory for the rest of the run
  4. Each result is converted into a `Flight` object — cheapest fare only, so `seats_left` and `arrival_time` are always `None`
  5. `scraper.py` returns the combined list of Flight objects; a single day's failed query is logged and skipped, not fatal to the whole scrape
6. `flight_search.py` calls `cache.py` write method to save the scraped flights to GCS (or locally, if GCS is unreachable)
7. `flight_search.py` merges all origin airports' flights, filters by date range, destination airports, and budget, then sorts per the sort mode — `date`: country, city, time; `price`: country, city, price, time
8. `display.py` prints filtered results as table, prepending a `Departure` column when more than one origin airport was searched

## Data Model

Flight:
```
origin_iata: str                # e.g. "EIN"
destination_iata: str           # e.g. "BCN"
destination_city: str           # e.g. "Barcelona"
destination_country: str        # e.g. "Spain"
airline: str                    # e.g. "Ryanair"
flight_number: str | None       # e.g. "FR1926"; not always available
departure_time: datetime
arrival_time: datetime | None   # Not available for all airlines
price_eur: float
currency: str = "EUR"           # kept for completeness; prices are normalised to EUR
seats_left: int | None = None   # airline's reported seats/fares left; None = not reported by source
scraped_at: datetime            # when THIS record was captured (defaults to now)
```

Cache files (GCS-first, these are the local fallback paths — see "Shared GCS Cache Convention" in ARCHITECTURE_DASHBOARD.md):
- `cache/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json` — list of Flight objects, 1 day TTL (scraping runs daily)

## Airport Data Reconciliation

Four files work together to classify destination IATA codes encountered during scraping:

| File                      | Type           | Purpose                                                   |
|---------------------------|----------------|-----------------------------------------------------------|
| `eu_airports.json`        | Static         | Canonical IATA → city/country lookup; the source of truth |
| `ignored_airports.json`   | Static         | IATA codes deliberately excluded from results (e.g. known non-EU or out-of-scope codes) — scraper skips these entirely, no logging needed |
| `unknown_airports.json`   | Auto-generated | IATA codes seen during scraping that are in neither file above — candidates to review and merge into `eu_airports.json` or `ignored_airports.json` |
| `ambiguous_airports.json` | Auto-generated | IATA codes that ARE in `eu_airports.json`, but where the airline's own response (city/country) disagrees with the static record — candidates to review and reconcile |

When `scraper.py` encounters a destination IATA code, it's classified as follows:
1. **Code is in `eu_airports.json**`
  - Airline response agrees with the static record → proceed normally, no logging
  - Airline response disagrees (different city/country) → log to `ambiguous_airports.json`, still proceed using the static record
2. **Code is not in `eu_airports.json**`
  - Code is in `ignored_airports.json` → skip this destination, nothing logged
  - Code is in neither file → log to `unknown_airports.json`

Both auto-generated files are periodically reviewed and merged by hand: `unknown_airports.json` entries go into `eu_airports.json` (if legitimate) or `ignored_airports.json` (if it should be excluded going forward); `ambiguous_airports.json` entries are reconciled directly in `eu_airports.json`.

## Key Design Decisions

| #   | Decision                                                                                         | Alternatives considered                                                        | Rationale                                                       |
|-----|--------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------|
| 001 | Scrape airline sites                                                                             | Skyscanner / Amadeus API                                                       | APIs require registration/payment; scraping is free             |
| 002 | Cache as local JSON file                                                                         | SQLite, Redis                                                                  | Simplest approach for a CLI tool                                |
| 003 | 1 week cache TTL                                                                                 | No cache, daily cache                                                          | Balance between freshness and avoiding frequent scrapes         |
| 004 | Support 6 airlines                                                                               | All European budget airlines                                                   | Covers majority of European budget routes                       |
| 005 | Static lookup table for IATA to city/country mapping                                             | External API                                                                   | Simpler, no API dependency, EU airports list is finite          |
| 006 | Scraping only, no airline APIs                                                                   | Ryanair unofficial API                                                         | Free tier limits too restrictive for practical use              |
| 007 | Web UI deferred until CLI is fully working                                                       | Build UI first                                                                 | CLI is the top priority; UI is a nice-to-have                   |
| 008 | Scrape 3 months + 1 week buffer upfront                                                          | Scrape per query                                                               | Reduces scraping frequency; budget/timerange applied as filters |
| 009 | `flight_search.py` orchestrates all modules                                                      | Merge logic into cache.py                                                      | Clear separation of concerns                                    |
| 010 | Cache filename uses `{origin}_{yyyymmdd}.json` under a day-scoped `{airline}/{yyyymm}/{dd}/` directory | Full timestamp; origin as a directory segment instead of filename prefix       | Date precision sufficient for the daily cache TTL (see #21); day-scoped directory makes freshness a plain existence check instead of scanning/parsing filenames (see #22) |
| 011 | `Flight` dataclass in `models.py`                                                                | Raw dicts                                                                      | Type safety and shared definition across modules                |
| 012 | `arrival_time` is optional (None)                                                                | Require arrival time                                                           | Not all airlines provide arrival time in scrape response        |
| 013 | Constants in `config.py`, helpers in `utils.py`                                                  | Inline in each module                                                          | Single source of truth, avoids duplication                      |
| 014 | Ryanair cookie cached as JSON, session bypassed                                                  | pickle, re-init every run                                                      | pickle is a security risk; re-init takes ~10 minutes            |
| 015 | Attempted calling Ryanair's endpoints directly instead of `ryanair-py` — route discovery + windowed `booking/v4/availability` (7-day windows, single-threaded with delay/retry, separately-cached route list), later even via Playwright/browser automation to work around persistent 409s | Continue using `ryanair-py` library                                            | Needed every flight per day (not just cheapest) plus `faresLeft`/seats-left for v2 flight analysis — but the endpoint proved unworkable: sustained bot-detection blocks that even real-browser automation only bypassed via a fragile, one-off UI-driven flow, not genuine API calls |
| 016 | Reverted to the `ryanair-py` library (`farfnd/v4/oneWayFares`, its cheapest-fare search) — dropping `booking/v4/availability`, route discovery, and all browser automation entirely; only the cheapest fare per destination per day is captured, one query per day (a single call spanning the whole 3-month range only returns one fare for the entire range, not a per-day breakdown) | Keep debugging the direct-API/Playwright approach                              | `oneWayFares` works reliably with a plain `requests` session, no anti-bot workaround needed — trades away per-flight/seats-left granularity for reliability; daily scraping now builds a price-history dataset over time instead of complete per-day data in one shot |
| 017 | Extend `Flight` with `origin_iata`, `flight_number`, `seats_left`, `currency`, `scraped_at`      | Keep v1's minimal Flight fields                                                | Needed for daily scraping and historical price-trend analysis (also consumed by the v2 pipeline); `seats_left` is currently always `None` since decision #16's data source doesn't provide it |
| 018 | No `Route` dataclass — dropped entirely, not kept for future use (supersedes an earlier `Route` dataclass that briefly existed in `scraper.py`, never in `models.py`) | Keep a dormant `Route` dataclass around for a future airline integration that might need route-discovery data | Decision #16 dropped route discovery entirely (destinations are a side effect of each day's cheapest-fares query); keeping an unused model around for a hypothetical future need is speculative — a future airline that genuinely needs route data can reintroduce it then, informed by that airline's actual API shape |
| 019 | Split airport discovery into `unknown_airports.json` (not in lookup) vs `ambiguous_airports.json` (in lookup, but data disagrees), and add `ignored_airports.json` to suppress known non-EU/out-of-scope codes | Single `unknown_airports.json` for all cases; silently drop unrecognised codes | Distinguishes genuinely new airports from data-mismatches needing reconciliation, and stops known out-of-scope codes from being re-logged on every scrape |
| 020 | v1 CLI checks GCS before scraping; falls back to the local file cache only if GCS is unreachable | Keep v1 fully local, no cloud dependency                                       | Lets an on-demand CLI search share scrape cost with the scheduled v2 pipeline (same GCS bronze layer) while still guaranteeing v1 works standalone with no GCP access — see ARCHITECTURE_DASHBOARD.md's "Shared GCS Cache Convention" |
| 021 | Flight cache TTL changed from 1 week to 1 day (supersedes decision #3)                           | Keep 1-week TTL                                                                | Scraping now runs daily; a 1-week TTL would skip re-scraping for 6 of every 7 days, defeating the point of a daily schedule |
| 022 | `cache.py`'s read/write and retry-queue functions take today's date as an explicit `yyyymmdd` string parameter, computed once by the caller, instead of each function calling `_utc_now()` internally | Each function computes its own `now()` internally                              | One `now()` read per logical operation avoids the GCS attempt and local fallback ever resolving to different dates at a midnight boundary; also lets the day-scoped path (#10) be built without re-deriving the date in multiple places |
| 023 | CLI redesigned to `departure destination timerange budget sort`: places accept IATA code, city, or country (a name resolves to ALL its airports — matched exact first, then unique substring, else rejected with "did you mean" suggestions); destination `none` = any; timerange is exact dates `yyyy-mm-dd...yyyy-mm-dd` (single date = that one day), replacing relative `{n}d/w/m`; sort modes `date`/`price`; IATA and names can't be mixed between departure and destination; sorting moved out of `display.py` into `filter_flights` so the sort mode is respected, and a `Departure` column appears for multi-airport searches | Keep relative timerange formats alongside exact dates; fuzzy auto-accept of typos; strict per-kind matching (city only pairs with city); one origin airport per run | Exact dates match how trips are planned and one format keeps parsing unambiguous; substring matching is forgiving without ever silently guessing wrong (fail loud); city/country searches map naturally onto multiple origin airports, kept traceable via the origin column |


## External Dependencies

**Active:**

| Name                 | Version | Purpose                                                                           | Docs                                                                               |
|----------------------|---------|-----------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| ryanair-py           | 3.0.0   | Fetches the cheapest one-way fare per destination per day from `farfnd/v4/oneWayFares`, via a plain `requests` session it manages internally — see decision #16 | [https://github.com/cohaolain/ryanair-py](https://github.com/cohaolain/ryanair-py) |
| requests             | latest  | Transitive dependency of `ryanair-py` (its `SessionManager`) — not called directly by this project's own code | [https://docs.python-requests.org](https://docs.python-requests.org)               |
| rich                 | latest  | Table display in terminal                                                         | [https://rich.readthedocs.io](https://rich.readthedocs.io)                         |
| google-cloud-storage | latest  | GCS-first cache check/write for v1 CLI (falls back to local cache if unreachable) | [https://cloud.google.com/python/docs/reference/storage/latest](https://cloud.google.com/python/docs/reference/storage/latest) |


**Deprecated:**

| Name       | Version  | Purpose                                                                                    | Reason removed |
|------------|----------|--------------------------------------------------------------------------------------------|----------------|
| playwright | >=1.47.0 | Tried to work around `booking/v4/availability`'s 409s via a real headless Chromium session | See decisions #15-16 — the endpoint itself was abandoned, not just the workaround, so this dependency is no longer needed for Ryanair. Kept in mind for other, JS-heavy airline sites (see Future table) |


**Future (not yet active):**

| Name           | Version | Purpose                          | Docs                                                                                           |
|----------------|---------|----------------------------------|------------------------------------------------------------------------------------------------|
| beautifulsoup4 | latest  | HTML parsing for future airlines | [https://www.crummy.com/software/BeautifulSoup](https://www.crummy.com/software/BeautifulSoup) |


## Constraints & Assumptions

- European airports only for departure
- Max search range is 3 months
- Prices may be stale up to 1 day due to caching (scraping runs daily)
- Airline websites/APIs may change structure and break the scraper
- Destination, budget, and date range are filter parameters applied after loading from cache, not scrape parameters
- `arrival_time` is always `None` for Ryanair — `ryanair-py`'s cheapest-fares endpoint doesn't provide it, not just "not all airlines provide it"
- `seats_left` is always `None` for Ryanair (see decision #16) — the cheapest-fares endpoint doesn't expose it, unlike the abandoned per-flight availability approach
- `currency` is always normalised to EUR at scrape time (via `Ryanair(currency="EUR")`) — the field is kept for completeness, not because non-EUR values are expected
- Only the cheapest fare per destination per day is captured, not every flight — daily scraping builds a price-history dataset over time rather than complete per-day flight data in one shot (see decision #16)
- Scraping queries once per calendar day in the buffer (not once for the whole 3-month range, which only returns a single cheapest fare across the entire range) — `SCRAPE_BUFFER_DAYS` queries per origin, each fast (~0.2-0.3s) via a plain `requests` session `ryanair-py` manages
- v1's cache check now depends on GCS when reachable; if GCS is unreachable (network/auth error), the CLI falls back to its local `cache/` folder and functions exactly as it did before GCS was introduced — no hard dependency on GCP access

## Open Questions

- [ ] Web interface: simple HTML page, or a framework like Flask/FastAPI + React?
- [ ] Should the web UI replace the CLI or run alongside it?
- [ ] Real-time search in the browser or trigger from CLI and view results in browser?
- [ ] How to handle scraping failures gracefully — skip airline or abort?
- [ ] What retry count and delay-between-requests should the windowed availability scraper use by default?
- [ ] Should a destination whose route-list entry hasn't been seen in N monthly refreshes be treated as dropped and removed from the route cache?

## Supported Airlines

| Airline   | Website       | Method                                         | Status    |
|-----------|---------------|------------------------------------------------|-----------|
| Ryanair   | ryanair.com   | `ryanair-py` library (`farfnd/v4/oneWayFares`) | ✅ v1      |
| easyJet   | easyjet.com   | Scraping                                       | 🔜 planned |
| Wizz Air  | wizzair.com   | Scraping                                       | 🔜 planned |
| Vueling   | vueling.com   | Scraping                                       | 🔜 planned |
| Norwegian | norwegian.com | Scraping                                       | 🔜 planned |
| Eurowings | eurowings.com | Scraping                                       | 🔜 planned |

## Non-Functional Requirements

| Concern              | Target / Constraint                   |
|----------------------|---------------------------------------|
| Latency              | ~~0.2-0.3s per day queried; a full scrape issues `SCRAPE_BUFFER_DAYS` (~~97) requests per origin, plus a configurable politeness delay between them; subsequent runs use cache |
| Cache TTL            | 1 day (flights) — scraping runs daily |
| Scraping concurrency | Single-threaded, with a small delay between requests (ryanair-py retries transient failures internally) |
| Max search range     | 3 months                              |
| Supported OS         | macOS, Linux                          |


---

## Decision Log (ADR summary)

| ADR | Decision                                                                                   | Status                                 |
|-----|--------------------------------------------------------------------------------------------|----------------------------------------|
| 001 | Use local JSON cache over database                                                         | Accepted                               |
| 002 | Scrape airlines directly, no paid or unofficial APIs                                       | Accepted                               |
| 003 | Support 6 major European budget airlines                                                   | Accepted                               |
| 004 | `flight_search.py` orchestrates all modules                                                | Accepted                               |
| 005 | Scrape 3 months + 1 week buffer, filter at display time                                    | Accepted                               |
| 006 | `Flight` dataclass defined in `models.py`, shared across modules                           | Accepted                               |
| 007 | `arrival_time` is optional — not all airlines provide it                                   | Accepted                               |
| 008 | Constants in `config.py`, shared helpers in `utils.py`                                     | Accepted                               |
| 009 | Ryanair cookie cached as JSON; session bypassed via `__new_`_                              | Accepted                               |
| 010 | Query Ryanair endpoints directly instead of via `ryanair-py`                               | Superseded by 021                      |
| 011 | Fetch direct route list before querying availability                                       | Superseded by 021                      |
| 012 | Cache route list separately with a 1 month TTL                                             | Superseded by 021                      |
| 013 | Query availability in 7-day windows per destination                                        | Superseded by 021                      |
| 014 | Single-threaded scraping with delay + retry                                                | Accepted                               |
| 015 | Extend `Flight` with fields for daily scraping/trend analysis                              | Accepted                               |
| 016 | New `Route` dataclass, separate from `Flight`                                              | Superseded (dropped, see decision #18) |
| 017 | Split airport handling into unknown/ambiguous/ignored files                                | Accepted                               |
| 018 | v1 CLI checks GCS before scraping; local file cache is fallback-only                       | Accepted                               |
| 019 | Flight cache TTL changed from 1 week to 1 day (scraping now runs daily)                    | Accepted                               |
| 020 | Tried Playwright (real Chromium) for Ryanair to work around `booking/v4/availability` 409s | Superseded by 021                      |
| 021 | Revert to `ryanair-py` (supersedes 010) — cheapest fare per destination per day via `farfnd/v4/oneWayFares`, no browser automation, no separate route-discovery step | Accepted                               |
| 022 | Cache path restructured to day-scoped `{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json` (provide a place for v2's pipeline summary); date passed explicitly into cache/retry-queue functions instead of each calling `_utc_now()` | Accepted                               |
| 023 | CLI v2: departure/destination as IATA/city/country with substring matching + suggestions, exact-date timerange (supersedes relative `{n}d/w/m`), destination filter, `date`/`price` sort modes | Accepted                               |