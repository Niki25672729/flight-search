# Architecture — v2 Data Engineering Pipeline

> v1 (the CLI) is documented separately in [ARCHITECTURE.md](./ARCHITECTURE.md) and is unaffected by anything in this document. v2 adds a batch ETL pipeline and BI dashboard in the same repo, on top of v1's scraper.

## Overview

v1 stays fully functional standalone — if GCS is unreachable, the CLI falls back entirely to its own local JSON cache (see [ARCHITECTURE.md](./ARCHITECTURE.md)). When GCS *is* reachable, though, v1 and v2 share the same GCS landing layer as a common cache: the CLI checks GCS for fresh data before scraping, and the scheduled pipeline checks GCS to avoid re-scraping an origin the CLI already fetched that day (see "Shared GCS Cache Convention" under Data Flow). v2 reuses v1's scraper/cache/models (in `src/`) as the ingestion source rather than re-implementing scraping logic, and adds cloud storage, distributed processing, a warehouse, transformation, orchestration, and a dashboard on top. All tools are chosen to run on free tiers or free trial credit, with GCP included deliberately.

## Goals & Non-Goals

**Goals**
- Land raw scraped flights in cloud storage (bronze) on a schedule, independent of the CLI's on-demand local cache
- Clean, dedupe, and type raw data with PySpark (silver)
- Model a star schema with dbt Core, with tests and docs (gold)
- Serve a Power BI dashboard on top of the gold tables
- Orchestrate the whole pipeline with Airflow
- Keep the CLI (v1) working exactly as before, sharing code rather than duplicating it
- Stay within free tiers / free trial credit — no ongoing cost

**Non-Goals**
- Real-time/streaming ingestion (batch only, e.g. daily)
- Removing the local CLI cache — it remains v1's fallback for when GCS is unreachable, so the CLI still works fully offline / without GCP access
- Production-grade scaling — this is a portfolio project, not a paid product
- Multi-cloud — GCP is the primary cloud; Databricks Community Edition is used only for its free Spark environment
- Running Airflow as an always-on cloud service long-term (cost) — see Orchestration decision below

## High-Level Diagram

```text
┌────────────┐   ┌──────────────┐   ┌─────────────┐   ┌────────────┐   ┌───────────┐   ┌───────────────┐
│ Ingestion  │──▶│ Landing      │──▶│ Processing  │──▶│ Warehouse  │──▶│ Transform │──▶│ BI dashboard  │
│ Scheduled  │   │ (bronze)     │   │ PySpark on  │   │ BigQuery   │   │ dbt Core  │   │ Power BI      │
│ CLI scraper│   │ GCS free tier│   │ Databricks  │   │ free tier  │   │ star      │   │ Desktop       │
│            │   │              │   │ Community Ed│   │            │   │ schema    │   │               │
└────────────┘   └──────────────┘   └─────────────┘   └────────────┘   └───────────┘   └───────────────┘
      ▲
      └── orchestrated by Airflow DAG (ingest → transform → dbt run), scheduled via Docker Compose locally
          or a short-lived Cloud Composer run on GCP trial credit
```

## Components

### `pipeline/ingestion/`

| Field          | Value                                                                    |
|----------------|--------------------------------------------------------------------------|
| Responsibility | Call v1's scraper (`src/scraper.py`) on a schedule (see ARCHITECTURE.md decision #16), and write raw responses to GCS as bronze. Before scraping an origin, checks whether today's exact-date blob already exists in GCS for it (e.g. the CLI already triggered an on-demand scrape earlier the same day) and skips it if so — the same shared GCS cache layer the CLI checks (see "Shared GCS Cache Convention" under Data Flow) |
| Inputs         | List of departure airports to scrape (config-driven)                     |
| Outputs        | Raw availability data written to GCS as NDJSON (one `Flight` record per line, not a single JSON array, so the later GCS→BigQuery load doesn't need reshaping). Flights are partitioned daily as `bronze/flights/{airline}/{origin}/{YYYYMM}/{YYYYMMDD}.json`. The `{airline}` segment is `ryanair` today but future-proofs multi-airline support |
| Key files      | `pipeline/ingestion/run.py`                                              |
| External calls | `scraper` (v1's `src/scraper.py`, imported directly — `src/` is the package root per `pyproject.toml`, so this is a flat `from scraper import scrape_ryanair`, not a `flight_search.` or `src.` prefixed import), Google Cloud Storage |

### `pipeline/transform/`

| Field          | Value                                                                    |
|----------------|--------------------------------------------------------------------------|
| Responsibility | PySpark job(s): read bronze route and flight JSON, clean/dedupe/type, write silver. Flight dedup uses `scraped_at` to keep the latest observation per flight per day |
| Inputs         | Bronze GCS path (routes and flights)                                     |
| Outputs        | Silver tables (Parquet/Delta) written to GCS, readable by BigQuery       |
| Key files      | `pipeline/transform/clean_flights.py`                                    |
| External calls | Databricks Community Edition (Spark runtime), Google Cloud Storage       |

### `pipeline/dbt/` (TBD)

| Field          | Value                                                                    |
|----------------|--------------------------------------------------------------------------|
| Responsibility | Model silver → gold star schema: `fact_flight_price` (grain: one row per flight per `scraped_at` day), , `dim_airport`, `dim_airline`, `dim_date`; tests and docs |
| Inputs         | Silver tables in BigQuery (routes and flights)                           |
| Outputs        | Gold tables in BigQuery, dbt docs site, test results                     |
| Key files      | `pipeline/dbt/models/`, `pipeline/dbt/dbt_project.yml`                   |
| External calls | BigQuery (via `dbt-bigquery` adapter)                                    |

### `pipeline/orchestration/`

| Field          | Value                                                                    |
|----------------|--------------------------------------------------------------------------|
| Responsibility | Define the DAG (ingest → transform → dbt run) and run it on a schedule   |
| Inputs         | None (time-triggered)                                                    |
| Outputs        | Pipeline run logs, task success/failure status, retries                  |
| Key files      | `pipeline/orchestration/dags/flight_pipeline_dag.py`, `pipeline/orchestration/docker-compose.yml` |
| External calls | Apache Airflow (self-hosted via Docker Compose, or Cloud Composer for short-lived managed runs) |

### `dashboards/powerbi/` (TBD)

| Field          | Value                                                                    |
|----------------|--------------------------------------------------------------------------|
| Responsibility | Visualize gold tables — price trends by route, cheapest destinations, price over time |
| Inputs         | Gold tables in BigQuery (via BigQuery connector)                         |
| Outputs        | `.pbix` dashboard file, exported screenshots for the portfolio           |
| Key files      | `dashboards/powerbi/flight_dashboard.pbix`                               |
| External calls | BigQuery                                                                 |

## Orchestration: Airflow

Because running Airflow as an always-on managed service has an ongoing cost, orchestration is split across two tracks:

| Track | What it's for | Cost |
|---|---|---|
| **Local Airflow** (Docker Compose) | Primary way to develop, run, and demo the DAG — screenshots/recordings for the portfolio, and where the DAG actually gets tested | Free forever |
| **Cloud Composer** (managed Airflow on GCP) | Optional stretch: spin up briefly on the $300/90-day trial credit to get genuine managed-Airflow experience, then tear down | Free within trial credit, must be deleted before it expires |

The DAG definition (`flight_pipeline_dag.py`) is written to run unmodified on either — same code, different executor environment. There's no dependency on GitHub Actions for orchestration in this design.

## Data Flow

1. Airflow DAG triggers on schedule (e.g. daily)
2. Task 1 — `pipeline/ingestion/run.py` calls v1's `scraper.scrape_ryanair` for each configured airport:
   1. Before querying a day, checks GCS for today's exact-date flights blob for that origin — if it already exists (e.g. the CLI already scraped it on demand earlier the same day), skips it for this run
   2. Cheapest-fare query: `ryanair-py`'s `get_cheapest_flights`, one query per day across the next 3 months + 1 week (see ARCHITECTURE.md decision #16)
   3. Raw responses are written to GCS bronze as NDJSON, following the partitioning described in [Components](#components) above
3. Task 2 — PySpark job (`pipeline/transform/`) reads bronze routes and flights, cleans/dedupes/types both, writes silver to GCS
4. Task 3 — silver data is loaded into BigQuery (native GCS-to-BigQuery load, or an external table over GCS)
5. Task 4 — `dbt run` (via `dbt-bigquery`) transforms silver → gold star schema in BigQuery (`fact_flight_price`, `dim_airport`, `dim_airline`, `dim_date`); `dbt test` validates it
6. Power BI Desktop refreshes against the gold BigQuery tables on demand, including price-over-time trends made possible by daily `scraped_at` snapshots
7. The v1 CLI shares the same GCS bronze layer as a cache (see "Shared GCS Cache Convention" below) rather than running fully independently of v2 — but degrades gracefully to its own local cache if GCS is unreachable

### Shared GCS Cache Convention

v1 and v2 read/write the same GCS bronze paths, but each with different intent:

- **v1 CLI** (see [ARCHITECTURE.md](./ARCHITECTURE.md)): before scraping, checks GCS for a flights blob within the *existing* 1-day freshness window (scraping runs daily), same TTL as today, just checked against GCS instead of local disk first — there's no separate route cache/freshness window anymore (see ARCHITECTURE.md decision #18: the `Route` dataclass and route caching were dropped entirely, not kept dormant). On a miss, it scrapes and writes the result to GCS. If GCS itself is unreachable (network/auth error — not just a miss), the CLI falls back entirely to its own local `cache/` folder for that run and never touches GCS.
- **Pipeline** (`pipeline/ingestion/`): before scraping an origin on its scheduled run, checks whether *today's exact-date* blob already exists in GCS for that origin — a narrower, same-day dedup check, not a freshness window — and skips scraping it again if so.

This lets an on-demand CLI search and the scheduled pipeline share scrape cost instead of duplicating it, while keeping each system's failure mode independent: the CLI never needs GCS to function, and the pipeline never depends on the CLI having been run.

## Key Design Decisions

| #  | Decision                                                    | Alternatives considered                  | Rationale                                                                 |
|----|--------------------------------------------------------------|-------------------------------------------|----------------------------------------------------------------------------|
| P1 | Reuse v1's scraper/cache (`src/`) as pipeline ingestion source | Separate scraper implementation for pipeline | One source of truth; avoids duplicated scraping logic and drift            |
| P2 | GCS for bronze/silver landing                                 | S3, Azure Blob                            | GCP free tier covers project scale |
| P3 | BigQuery as the warehouse                                     | Databricks SQL warehouse, Snowflake       | Generous free tier (10 GB storage, 1 TB query/month); no credit card trial expiry risk |
| P4 | Databricks Community Edition for PySpark                      | Local PySpark only, Dataproc              | Free forever; at the cost of no built-in job scheduling |
| P5 | dbt Core (not dbt Cloud)                                       | dbt Cloud free tier                       | No login/seat limits; dbt isn't an orchestrator either way, so this doesn't affect the orchestration choice |
| P6 | Apache Airflow for orchestration                               | GitHub Actions, Databricks Workflows, dbt Cloud scheduler | Databricks Workflows isn't available on Community Edition; GitHub Actions doesn't showcase orchestration-specific skills |
| P7 | Airflow runs locally via Docker Compose, Cloud Composer optional/short-lived | Always-on Cloud Composer                  | Always-on managed Airflow isn't free; local Docker Compose is free forever and sufficient to develop and demo the DAG |
| P8 | Power BI Desktop only, not Power BI Service                    | Power BI Service (Pro)                    | Building/viewing dashboards is free in Desktop; publishing to the cloud service requires a paid license |
| P9 | v2 lives in the same repo as v1, as an additive `pipeline/` and `dashboards/` layer, documented in a separate file | Single combined ARCHITECTURE.md; separate repository | Keeps each system's docs self-contained and independently versioned, while still sharing code and portfolio narrative |
| P10 | v1 CLI checks GCS before scraping; local cache is fallback-only, used when GCS itself is unreachable | Keep v1 and v2 caches fully separate (original design) | Sharing a single GCS cache lets an on-demand CLI search and the scheduled pipeline avoid duplicating scrape cost, while local cache still guarantees v1 works with no GCP access at all |
| P11 | Pipeline skips an origin if today's exact-date GCS blob for it already exists | Always re-scrape every origin on every scheduled run | Avoids duplicate scraping when the CLI has already triggered an on-demand scrape for that origin earlier the same day |
| P12 | GCS → BigQuery ingestion (loading raw bronze into BigQuery) is a separate, later task/ADR, not decided alongside the GCS cache design | Design the BigQuery table/partitioning scheme now | Decouples the landing-layer design (which both v1 and v2 depend on today) from the warehouse-schema design (which only v2 needs, and can be iterated on independently) |

## External Dependencies

**Planned, not yet active:**

| Name              | Purpose                                    | Docs                                                       |
|-------------------|--------------------------------------------|------------------------------------------------------------|
| pyspark           | Distributed data cleaning/transformation   | https://spark.apache.org/docs/latest/api/python/         |
| google-cloud-storage | Read/write bronze and silver data to GCS | https://cloud.google.com/python/docs/reference/storage/latest |
| google-cloud-bigquery | Load silver data, run queries against gold | https://cloud.google.com/python/docs/reference/bigquery/latest |
| dbt-core / dbt-bigquery | Transform silver → gold, testing, docs | https://docs.getdbt.com                                  |
| apache-airflow    | DAG definition and orchestration           | https://airflow.apache.org/docs/                           |

## Constraints & Assumptions

- Batch only — pipeline runs on a schedule (e.g. daily), not real-time
- Free-tier ceilings apply and are treated as hard limits, not soft targets: GCS 5 GB, BigQuery 10 GB storage / 1 TB queries per month
- Databricks Community Edition has a single cluster, auto-terminates after idle time, and has no built-in job scheduler — scheduling is handled entirely by the Airflow DAG instead
- Airflow runs locally via Docker Compose by default; any Cloud Composer usage is time-boxed to the GCP trial period and must be torn down before it expires
- Power BI dashboards are built and viewed in Desktop; sharing is via exported file/screenshots, not a hosted service
- v1's CLI and the pipeline share the GCS bronze layer as a common cache (see "Shared GCS Cache Convention"); v1 only falls back to its own local file cache when GCS itself is unreachable, not merely when data is stale — the pipeline's ingestion runs regardless of CLI activity
- Ingestion queries one day at a time (`ryanair-py`'s `get_cheapest_flights`, see ARCHITECTURE.md decision #16) across the ~97-day scrape buffer, so total ingestion task duration scales with `SCRAPE_BUFFER_DAYS` per origin, not with the number of routes/destinations — this should factor into the Airflow DAG's schedule interval and task timeout

## Open Questions

- [ ] Load silver → BigQuery via native GCS load job, or query GCS directly as a BigQuery external table?
- [ ] Should `pipeline/ingestion` write one bronze file per scrape run, or append/merge into a single partitioned dataset?
- [ ] If GCP trial credit is used for anything beyond Cloud Composer (e.g. Dataproc Serverless instead of Databricks CE), how to ensure resources are torn down before the trial expires?
- [ ] BigQuery table/partitioning design for the raw bronze data (e.g. a `raw_flights` table, partitioned by scrape date?) — deferred to when the GCS → BigQuery ingestion task is built (see ADR P12)

## Decision Log (ADR summary)

| ADR  | Decision                                                                 | Status   |
|------|--------------------------------------------------------------------------|----------|
| 01   | v2 pipeline reuses v1's scraper/cache (`src/`), no duplicate scraper     | Accepted |
| 02   | GCS for bronze/silver landing (GCP over AWS/Azure)                       | Accepted |
| 03   | BigQuery as the warehouse                                                | Accepted |
| 04   | Databricks Community Edition for PySpark processing                      | Accepted |
| 05   | dbt Core (not dbt Cloud) for transformation                              | Accepted |
| 06   | Apache Airflow for orchestration, self-hosted via Docker Compose         | Accepted |
| 07   | Cloud Composer used only as a short-lived, trial-credit-bound stretch goal | Accepted |
| 08   | Power BI Desktop only, no Power BI Service                               | Accepted |
| 09   | v2 documented in a separate file (`ARCHITECTURE_PIPELINE.md`), own ADR numbering | Accepted |
| 10   | Ingestion reuses v1's scraper unchanged                                  | Accepted |
| 11   | v1 CLI checks GCS before scraping; local cache is fallback-only for when GCS is unreachable | Accepted |
| 12   | Flights partitioned daily per airline/origin                             | Accepted |
| 13   | Pipeline skips an origin already scraped same-day in GCS, avoiding duplicate scraping vs. the CLI | Accepted |
| 14   | GCS → BigQuery ingestion task/schema design deferred to a later ADR      | Accepted |
