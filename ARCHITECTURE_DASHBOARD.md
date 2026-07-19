# Architecture — v2 Data Engineering Pipeline

> v1 (the CLI) is documented separately in [ARCHITECTURE.md](./ARCHITECTURE.md) and is unaffected by anything in this document. v2 adds a batch ETL pipeline and BI dashboard in the same repo, on top of v1's scraper.

## Overview

v1 stays fully functional standalone — if GCS is unreachable, the CLI falls back entirely to its own local JSON cache (see [ARCHITECTURE.md](./ARCHITECTURE.md)). When GCS *is* reachable, though, v1 and v2 share the same GCS landing layer as a common cache: the CLI checks GCS for fresh data before scraping, and the scheduled pipeline checks GCS to avoid re-scraping an origin the CLI already fetched that day (see "Shared GCS Cache Convention" under Data Flow). v2 reuses v1's scraper/cache/models (in `src/`) as the ingestion source rather than re-implementing scraping logic, and adds cloud storage, processing, a warehouse, transformation, orchestration, and a dashboard on top. Tool choices are constrained to free tiers or free trial credit, and each is also chosen to fit its role in the pipeline (see Key Design Decisions).

## Goals & Non-Goals

**Goals**
- Land raw scraped flights in cloud storage (bronze) on a schedule, sharing the same GCS bronze layer as the CLI's cache (see Shared GCS Cache Convention)
- Clean, dedupe, and type raw data with PySpark (silver)
- Model a star schema with dbt Core, with tests and docs (gold)
- Serve a Looker Studio dashboard on top of the gold tables, connected directly to BigQuery
- Orchestrate the whole pipeline with Airflow
- Keep the CLI (v1) working exactly as before, sharing code rather than duplicating it
- Stay within free tiers / free trial credit — no ongoing cost

**Non-Goals**
- Real-time/streaming ingestion (batch only, e.g. daily)
- Removing the local CLI cache — it remains v1's fallback for when GCS is unreachable, so the CLI still works fully offline / without GCP access
- Production-grade scaling — data volume (single-digit GB, daily batch) doesn't warrant it
- Multi-cloud — GCP is the sole cloud provider; PySpark runs locally rather than via an external distributed Spark service
- Running Airflow as an always-on cloud service long-term (cost) — see decision #6

## High-Level Diagram

```text
┌───────────┐   ┌─────────┐   ┌────────────┐
│ Ingestion │──▶│ Landing │◀─▶│ Processing │
└───────────┘   └────┬────┘   └────────────┘
                     │
                     ▼
               ┌───────────┐   ┌───────────┐
               │ Warehouse │◀─▶│ Transform │
               └─────┬─────┘   └───────────┘
                     │
                     ▼
               ┌───────────┐
               │ Dashboard │
               └───────────┘
```

- Orchestrated by an Airflow DAG, scheduled via Docker Compose locally
- Each task (Ingestion, Processing, Transform) has its own container
- Processing reads bronze from Landing and writes silver back to Landing (as Parquet)
- Warehouse exposes that silver as a lightweight external-table pointer (not a load job) for Transform to query and materialize into gold natively

## Components

### `infrastructure/terraform/`

| Field          | Value                                                            |
| -------------- | ---------------------------------------------------------------- |
| Responsibility | Provision shared GCP resources: the bronze/silver GCS bucket (90-day lifecycle rule on `bronze/`) and a service account with `storage.objectAdmin`, granted via impersonation rather than a downloaded key |
| Inputs         | Terraform variables (`terraform.tfvars`, gitignored)             |
| Outputs        | GCS bucket + service account that the CLI (`src/cache.py`) and pipeline (`pipeline/ingestion/run.py`) authenticate against |
| Key files      | `infrastructure/terraform/main.tf`, `variables.tf`, `outputs.tf` |
| External calls | GCP Storage + IAM APIs, via the `hashicorp/google` provider      |

### `infrastructure/docker/`

| Field          | Value                                                                              |
| -------------- | ---------------------------------------------------------------------------------- |
| Responsibility | Dockerfiles for each per-task container: Airflow, ingestion, processing, transform |
| Inputs         | N/A (build-time definitions)                                                       |
| Outputs        | Container images that Airflow launches via `DockerOperator`                        |
| Key files      | `infrastructure/docker/{task}/Dockerfile`                                          |
| External calls | None (local Docker builds)                                                         |

### `infrastructure/airflow/`

| Field          | Value                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------- |
| Responsibility | Define the DAG (ingest → retry → report today; processing/transform join once built), run it on a schedule, and alert on DagRun failure |
| Inputs         | None (time-triggered)                                                                       |
| Outputs        | Pipeline run logs, task success/failure status, retries, a per-origin/aggregate ingestion summary, and an ERROR-level alert log line on DagRun failure (see Monitoring & Alerting) |
| Key files      | `infrastructure/airflow/dags/flight_pipeline_dag.py`, `callbacks.py` (DagRun-failure alert) |
| External calls | Docker Engine (via `DockerOperator`)                                                        |

### `pipeline/ingestion/`

| Field          | Value                                                                      |
| -------------- | -------------------------------------------------------------------------- |
| Responsibility | `ingest_airport` calls v1's scraper (`src/scraper.py`) per configured airport and writes bronze to GCS<br>`retry_failed_ingests` re-attempts previously failed {origin, date} pairs from the retry queue and merges recovered flights |
| Inputs         | `SCRAPE_ORIGINS` (hardcoded in `src/config.py` — see decision #008)        |
| Outputs        | Raw availability data written to GCS as NDJSON. Flights are partitioned daily as `bronze/flights/{airline}/{yyyymm}/{dd}/{origin}_{yyyymmdd}.json`. |
| Key files      | `pipeline/ingestion/run.py` (scheduled entry point — one origin or `retry` per invocation, see Airflow DAG), `pipeline/ingestion/manual_run.py` (manual/hotfix CLI entry point) |
| External calls | `scraper` (v1's `src/scraper.py`, imported directly), Google Cloud Storage |

### `pipeline/processing/` (TBD)

| Field          | Value                                                                     |
| -------------- | ------------------------------------------------------------------------- |
| Responsibility | PySpark job: read bronze flights, clean/dedupe/type, write silver         |
| Inputs         | Bronze GCS path (flights)                                                 |
| Outputs        | Silver (Parquet) written to GCS, exposed to BigQuery as an external table |
| Key files      | TBD                                                                       |
| External calls | Google Cloud Storage                                                      |

### `pipeline/transform/` (TBD)

| Field          | Value                                                              |
| -------------- | ------------------------------------------------------------------ |
| Responsibility | Model silver → gold star schema (exact fact/dimension tables TBD, pending dashboard requirements); tests and docs |
| Inputs         | Silver flights, exposed as a BigQuery external table over GCS      |
| Outputs        | Gold tables in BigQuery (native), dbt docs site, test results      |
| Key files      | `pipeline/transform/models/`, `pipeline/transform/dbt_project.yml` |
| External calls | BigQuery (via `dbt-bigquery` adapter)                              |

### `dashboards/looker/` (TBD)

| Field          | Value                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------- |
| Responsibility | Visualize gold tables — price trends by destination, cheapest destinations, price over time |
| Inputs         | Gold tables in BigQuery (native connector, live or scheduled refresh)                       |
| Outputs        | Looker Studio report, shareable link (public or restricted)                                 |
| Key files      | N/A (cloud-native report, no local project file)                                            |
| External calls | BigQuery                                                                                    |

## Data Flow

1. Airflow DAG triggers on schedule (e.g. daily)
2. Task 1 — ingestion: one Airflow-mapped task per origin, each launching its own `pipeline/ingestion/run.py` container that calls `ingest_airport` for that origin and writes bronze to GCS; `retry_failed_ingests` then re-attempts previously failed {origin, date} pairs from the retry queue, followed by a reporting task that logs and persists a per-origin/aggregate summary of the run (see Monitoring & Alerting)
3. Task 2 — PySpark job (`pipeline/processing/`) reads bronze flights, cleans/dedupes/types, writes silver (Parquet) to GCS
4. Task 3 — `dbt run` (`pipeline/transform/`, via `dbt-bigquery`) queries silver through a BigQuery external table over the GCS path (a lightweight pointer, not a load job) and materializes gold star schema natively in BigQuery (exact schema TBD, pending dashboard requirements); `dbt test` validates it
5. Looker Studio (`dashboards/looker/`) connects directly to the gold BigQuery tables (native connector, live or scheduled refresh) — no separate export step needed

### Shared GCS Cache Convention

v1 and v2 read/write the same GCS bronze paths, but each with different intent:

- **v1 CLI** (see [ARCHITECTURE.md](./ARCHITECTURE.md)): checks GCS for a fresh flights blob before scraping (existing 1-day TTL, just checked against GCS instead of local disk first); falls back to its local `cache/` folder only if GCS itself is unreachable, never on a normal miss
- **Pipeline** (`pipeline/ingestion/`): before scraping an origin on its scheduled run, checks whether *today's exact-date* blob already exists in GCS for that origin and skips scraping it again if so.

This lets an on-demand CLI search and the scheduled pipeline share scrape cost instead of duplicating it, while keeping each system's failure mode independent: the CLI never needs GCS to function, and the pipeline never depends on the CLI having been run.

## Silver Schema

`pipeline/processing/` turns each day's bronze into two small, purpose-built outputs rather than one big cleaned copy of bronze. Both share:

```
flight_key = hash(origin, destination, departure_date, airline)
```

— the identity of *one route-day's cheapest fare*, matching the only grain the source can see: `get_cheapest_flights` returns exactly one fare per (route, departure day) — verified against live data (131,154 snapshot rows = 131,154 distinct route-days, zero collisions). `departure_time` and `flight_number` are **attributes** of whichever physical flight is currently cheapest; they may change under a stable key, exactly like price. This keeps price history continuous when a cheaper flight on the same route-day displaces the current one — a price change, not a key break.

| Output                 | Grain                                                                | Written                                                                            | Why |
| ---------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | --- |
| `flights_latest_state` | 1 row / `flight_key` × `scrape_date`                                 | Partitioned by `scrape_date`, new partition daily (never overwrites a prior day's) | A day's cleaned bronze already *is* the complete current state (the scraper re-queries the full ~97-day window every run); "current" = the newest `scrape_date` partition, a cheap partition-pruned read. Partitioning keeps every write create-only — see "Idempotency & Write Ordering". |
| `flight_price_history` | 1 row / `flight_key` × `scrape_date`, only when new or price-changed | Appended daily                                                                     | The day-over-day diff (`prior_price_eur`, `is_new_flight`), computed once in Spark instead of repeatedly in billed BigQuery scans — its value is scan economy and price-event semantics, not compression (40–81% of fares reprice daily). Carries `airline` ahead of multi-airline scraping. |

### Idempotency & Write Ordering

Retry safety rests on two mechanisms (both hardened during a data-engineer design review before implementation):

**"Prior" is resolved as the most recent partition *strictly before* `run_date`** (`read_prior_latest_state`), never as "whatever's currently there." That single rule makes any retry, manual rerun, or backfill safe in every interleaving: a rerun always excludes `run_date`'s own partition, so a day can never be diffed against itself and the diff can never silently collapse to zero changes. (The today-vs-today hazard was real only in the original whole-table-overwrite design, where "prior" could only mean the table's current contents — the partitioned layout plus this read rule is what closed it.)

The job still writes the `flight_price_history` diff *before* today's `flights_latest_state` partition, but that ordering is a reader-consistency nicety, not a safety mechanism: correctness doesn't depend on it (the strictly-before read above does that work), it just guarantees a concurrent reader — e.g. a `dbt run` racing the Spark job — can never observe a new snapshot partition whose matching price-history partition doesn't exist yet.

**Every write must be partition-overwrite, not blind append or whole-table overwrite.** "Appended daily" for `flight_price_history` means a Spark `.mode("append")` would duplicate that day's rows if the task retries after a partial failure. Both outputs — including `flights_latest_state`, partitioned by `scrape_date` rather than a single mutable table — instead overwrite only the `scrape_date` partition being (re)computed (`partitionBy("scrape_date")` with `spark.sql.sources.partitionOverwriteMode=dynamic` and `.mode("overwrite")`), so re-running the same `run_date` replaces that day's rows instead of accumulating duplicates, and never touches a different day's partition.

### Worked example (AGP→EMA, real data)

*(Illustration from real data, verified manually at design time. CI asserts these behaviors as invariants over synthetic bronze — see tests/test_processing.py's Diff Invariants — rather than pinning these exact euro amounts to a committed data blob.)*

Bronze (raw scraped rows, 3 of 97 daily records for this route shown):

| scrape date | flight_number | departure_time | price_eur |
| ----------- | ------------- | -------------- | --------- |
| 07-07       | FR4459        | 07-10 07:10    | 18.99     |
| 07-07       | FR4459        | 07-11 05:45    | 27.99     |
| 07-08       | FR4459        | 07-10 07:10    | 18.99     |
| 07-08       | FR4459        | 07-11 05:45    | 27.80     |
| 07-08       | FR4459        | 07-28 18:25    | 53.97     |
| 07-09       | FR4459        | 07-10 07:10    | 18.99     |
| 07-09       | FR4459        | 07-11 05:45    | 40.95     |
| 07-09       | FR4459        | 07-28 18:25    | 53.55     |

`flight_price_history` (changed-only):

| scrape_date | flight_number | departure_time | price_eur | prior_price_eur | is_new_flight |
| ----------- | ------------- | -------------- | --------- | --------------- | ------------- |
| 07-08       | FR4459        | 07-11 05:45    | 27.80     | 27.99           | false         |
| 07-08       | FR4459        | 07-28 18:25    | 53.97     | —               | **true**      |
| 07-09       | FR4459        | 07-11 05:45    | 40.95     | 27.80           | false         |
| 07-09       | FR4459        | 07-28 18:25    | 53.55     | 53.97           | false         |

(`FR4459 @ 07-10 07:10` stays €18.99 all 3 days — correctly produces no rows, ever. Also verified against this data: `FR4459 @ 2026-08-04 18:25` (€68.05 on 07-07) is replaced on 07-08 by `FR4469 @ 10:50` (€68.99) — same route-day, so it lands as a €68.05 → €68.99 change with the `flight_number`/`departure_time` attributes moving, not a key break; encoded in `test_displacement_within_a_route_day_is_a_price_change`.)

## Retry Strategy

Failures are retried at two different layers — Airflow task retries and an in-process, CAS-protected code-level retry queue — rather than either one alone.

**Why not one Airflow task per (origin, date)?** Task granularity follows the origin, not the (origin, date) pair. Mapping one task per (origin, date) would multiply `SCRAPE_ORIGINS` (49) by `SCRAPE_BUFFER_DAYS` (~97) into ~4,750 task instances per DagRun — unmanageable to read, monitor, or debug in the Airflow UI, and far more scheduler overhead than the work justifies. One task per origin keeps the graph at 49 mapped instances; `scrape_ryanair()` loops the ~97 days in-process instead.

**Why not rely only on Airflow's task-level retry?** Follows directly from the above: since a single date isn't its own task, a single failed day within one origin's scrape has no Airflow task of its own to retry. Without the code-level retry queue, the only way to recover one failed day would be to fail the *entire* task and let Airflow rerun the whole ~97-day scrape for that origin — multiplying API calls ~97x to recover a single day, worsening exactly the rate-limiting/`403` risk this is meant to mitigate.

**Why not rely only on code-level retry?** The retry queue only runs while the container's process is alive — it can't recover from the container itself dying (crash, OOM, `execution_timeout`, an unreachable Docker socket). Something outside the process has to notice and relaunch it; that's Airflow's task-level retry (with exponential backoff, capped at `max_retry_delay`). Code-level and Airflow-level retry cover disjoint failure modes: one failed HTTP call mid-scrape vs. the whole process dying.

**Where CAS fits in.** GCS's compare-and-swap write (`if_generation_match`) isn't a retry mechanism — it protects the two retry layers above from corrupting each other's output. A zombie container (killed by Airflow but still alive and writing) can race a freshly-retried container for the same origin's cache/retry-queue blob; CAS is what stops one writer from silently clobbering a newer write, a problem neither retry layer has any mechanism to prevent on its own.

## Monitoring & Alerting

- **Alerting**: the DAG's `on_failure_callback` (`callbacks.py`) logs one ERROR-level line per failed DagRun, naming every failed task. No email/Slack delivery yet — deliberately deferred until a log line proves insufficient.
- **Monitoring**: a reporting task logs (and persists to GCS) a per-origin and aggregate summary of each run — flight counts, query success rate, a day-over-day comparison — escalating to WARNING when any origin is partial or failed. It never fails the DagRun itself.
- Both are covered by unit tests exercising the DAG's structure and callback/report logic directly, via a dedicated Airflow install (pyproject's `airflow` extra).

## Key Design Decisions

| #   | Decision                                                                                             | Alternatives considered                                                             | Rationale                                                       |
| --- | ---------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 001 | v2 lives in the same repo as v1, as an additive `pipeline/`, `dashboards/`, and `infrastructure/` layer, documented in a separate file | Single combined ARCHITECTURE.md; separate repository                                | Shares `src/` and repo-wide conventions (CLAUDE.md, CI, dependency management) with v1 instead of duplicating them across two repos, while separate doc files (`ARCHITECTURE.md` vs `ARCHITECTURE_DASHBOARD.md`) keep each system's design reasoning independently readable without cross-referencing every decision |
| 002 | Terraform provisions the GCS bucket and service account (`infrastructure/terraform/`)                | Manual setup via `gcloud`/console                                                   | Reproducible, versioned, self-documenting infra; matches the free-tier setup exactly and can be torn down/recreated without manual steps |
| 003 | Developer impersonates the service account (`roles/iam.serviceAccountTokenCreator`) rather than a downloaded key | Downloaded service account key (`.json`)                                            | Org policy blocks key creation; impersonation avoids a long-lived credential file to protect entirely |
| 004 | Each pipeline task (ingestion, processing, transform) runs in its own container                      | One container for everything; isolate every task uniformly ("one process per container" as a blanket rule) | Isolation is warranted by a real discriminator — a different runtime (PySpark needs a JVM) or a dependency-conflict risk with Airflow's own pinned packages (`dbt-core`, `ryanair-py`/`google-cloud-storage` version pins) — not by isolating for its own sake |
| 005 | Apache Airflow for orchestration                                                                     | GitHub Actions, Databricks Workflows, dbt Cloud scheduler                           | Airflow gives task-level retries, backfills, and dependency-aware scheduling needed to run ingest → processing → transform as one DAG with per-task state; GitHub Actions is a CI/CD tool without native backfill/catchup, and the dbt Cloud scheduler only covers the dbt step, not the whole pipeline. Databricks Workflows isn't available on Community Edition |
| 006 | Airflow runs locally via Docker Compose                                                              | Always-on Cloud Composer                                                            | Always-on managed Airflow isn't free; local Docker Compose is free forever and sufficient to develop and demo the DAG |
| 007 | Reuse v1's scraper/cache (`src/`) as pipeline ingestion source                                       | Separate scraper implementation for pipeline                                        | One source of truth; avoids duplicated scraping logic and drift |
| 008 | Scrape-origins list stays a hardcoded Python constant (`SCRAPE_ORIGINS` in `src/config.py`)          | GCS-hosted config file (`scrape_origin.json`, checked GCS-first with local fallback) — implemented, then reverted; Airflow Variable | Tried the GCS-hosted version: `cache.py` is the only module allowed to depend on `google-cloud-storage` (see decision #004), but the DAG needs `SCRAPE_ORIGINS` at parse time from a module copied into the Airflow image *without* that dependency. Landing the origins list anywhere the DAG imports from would force either installing `google-cloud-storage` in the Airflow image (reopening #004's exact dependency-conflict risk) or keeping two separate origins sources (DAG's static list vs. ingestion's GCS-checked one) that could drift. Not worth the complexity for a list of ~50 airport codes that changes rarely — reverted to the plain constant |
| 009 | GCS for bronze/silver landing                                                                        | S3, Azure Blob                                                                      | Same-cloud integration with BigQuery (native external-table support over GCS, no cross-cloud auth or egress); free tier covers current data volume |
| 010 | v1 CLI checks GCS before scraping; local cache is fallback-only, used when GCS itself is unreachable | Keep v1 and v2 caches fully separate (original design)                              | Sharing a single GCS cache lets an on-demand CLI search and the scheduled pipeline avoid duplicating scrape cost, while local cache still guarantees v1 works with no GCP access at all |
| 011 | Pipeline skips an origin if today's exact-date GCS blob for it already exists                        | Always re-scrape every origin on every scheduled run                                | Avoids duplicate scraping when the CLI has already triggered an on-demand scrape for that origin earlier the same day |
| 012 | Bronze is not loaded into or exposed in BigQuery — only silver (external table) and gold (native) reach it | Expose raw bronze in BigQuery (e.g. a `raw_flights` table)                          | Nothing in the pipeline needs it — Processing reads bronze directly from GCS; would just be an unused warehouse surface with no consumer today |
| 013 | Local PySpark (`local[*]` mode)                                                                      | Databricks Community Edition, Dataproc                                              | No external account/cluster dependency and nothing to spin up; at this project's data volume (single-digit GB, daily batch) there's no functional need for a managed cluster (containerized separately from Airflow per decision #4) |
| 014 | Silver stays in GCS as Parquet, not written directly to BigQuery                                     | PySpark writes directly to BigQuery via the `spark-bigquery-connector`              | The connector stages writes through GCS internally anyway, so it doesn't remove the GCS dependency — just adds one more piece of infrastructure. Also keeps a consistent bronze+silver-in-the-lake / gold-in-the-warehouse medallion pattern, with silver durable and warehouse-independent |
| 015 | BigQuery as the warehouse                                                                            | Databricks SQL warehouse, Snowflake                                                 | Serverless (no cluster sizing), columnar storage suited to the star-schema query pattern here, and native `dbt-bigquery` + GCS integration within the same GCP project/IAM boundary already used elsewhere; free tier persists indefinitely, unlike Snowflake's time-boxed trial which would force a later migration |
| 016 | Silver is exposed to BigQuery as an external table over GCS, not loaded into a native table          | Native GCS → BigQuery load job before each `dbt run`                                | An external table is a near-free DDL pointer, not a data-moving job; `dbt run` already does the real read-and-transform work when it queries the external table and materializes gold, so a separate load step would just be redundant data movement |
| 017 | dbt Core (not dbt Cloud)                                                                             | dbt Cloud free tier                                                                 | No login/seat limits; Airflow already handles scheduling for the whole pipeline, so dbt Cloud's built-in scheduler isn't needed |
| 018 | Looker Studio dashboard, connected directly to BigQuery                                              | Tableau Public (+ Sheets export), Power BI Desktop                                  | Native BigQuery connector needs no export/bridge step, unlike Tableau Public which can't connect to BigQuery directly; free, and its sharing model supports both public links and restricted access, unlike Tableau Public (public-only) or Power BI Desktop (no live link without a paid Service license) |
| 019 | CI (`.github/workflows/ingestion-image.yml`) builds and pushes the ingestion image to GHCR on every relevant push to `main`, but local Docker Compose/Airflow keep building `:dev` locally rather than pulling from GHCR | Switch local Docker Compose/DAG to pull the GHCR image instead of building locally  | Pulling from GHCR would slow local iteration (commit → push → wait for CI → pull, vs. an immediate local rebuild) for no benefit while the pipeline is still under active development. Revisit once v2 (processing/transform/dashboard) is finished and local iteration speed matters less than running a CI-verified image |
| 020 | DagRun-failure alerting logs a single line rather than emailing/paging; a separate always-succeeding task (not `retry_failed_ingests`) handles per-origin/aggregate reporting | SMTP/Slack integration in Docker Compose; folding stats into `retry_failed_ingests` | No mail server exists in the stack yet; a log line is a cheap floor, and a separate task keeps per-origin visibility without blocking retries |
| 021 | `flight_key = sha2(origin \| destination \| departure_date \| airline)` — the identity of one route-day's cheapest fare, enforced end-to-end (silver's derivation and uniqueness gate, ingestion's retry-merge dedupe) | The original physical-flight key (`+ departure_time + flight_number`, no airline); Python's `hash()` (per-process-randomized, breaks rerun idempotency) | The feed returns one cheapest fare per route-day, so a finer-grained key manufactures churn instead of identity: displacement read as a fake removal + fake new flight, and a retry observing a different cheapest broke the grain outright (2026-07-12). Under the route-day key, displacement is a price change on a stable key; `departure_time`/`flight_number` are attributes. Grain verification and details in the Silver Schema section above |

## External Dependencies

**Active:**

| Name                      | Purpose                                            | Docs                                                                 |
| ------------------------- | -------------------------------------------------- | -------------------------------------------------------------------- |
| terraform                 | IaC tool (provisions GCS bucket + service account) | https://developer.hashicorp.com/terraform                            |
| terraform-provider-google | GCP provider plugin for Terraform                  | https://registry.terraform.io/providers/hashicorp/google/latest/docs |
| google-cloud-storage      | Read/write bronze and silver data to GCS           | https://cloud.google.com/python/docs/reference/storage/latest        |

**Planned, not yet active:**

| Name                            | Purpose                                                        | Docs                                                                              |
| ------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| pyspark                         | Data cleaning/transformation, local Spark session (no cluster) | https://spark.apache.org/docs/latest/api/python/                                  |
| dbt-core / dbt-bigquery         | Transform silver → gold, testing, docs                         | https://docs.getdbt.com                                                           |
| apache-airflow                  | DAG definition and orchestration                               | https://airflow.apache.org/docs/                                                  |
| docker (Docker Engine)          | Container runtime for per-task containers                      | https://docs.docker.com/engine/                                                   |
| apache-airflow-providers-docker | `DockerOperator`, to launch containers from Airflow            | https://airflow.apache.org/docs/apache-airflow-providers-docker/stable/index.html |

## Constraints & Assumptions

- Batch only — pipeline runs on a schedule (e.g. daily), not real-time
- Free-tier ceilings apply and are treated as hard limits, not soft targets: GCS 5 GB, BigQuery 10 GB storage / 1 TB queries per month
- Local PySpark runs as a single-machine process — no horizontal scaling; sufficient at this project's data volume (single-digit GB, daily batch)
- Airflow runs locally via Docker Compose by default; any Cloud Composer usage is time-boxed to the GCP trial period and must be torn down before it expires
- Looker Studio connects directly to BigQuery; sharing can be a public link or restricted to specific viewers
- v1's CLI and the pipeline share the GCS bronze layer as a common cache (see "Shared GCS Cache Convention"); v1 only falls back to its own local file cache when GCS itself is unreachable, not merely when data is stale — the pipeline's ingestion runs regardless of CLI activity
- Ingestion queries one day at a time (`ryanair-py`'s `get_cheapest_flights`, see ARCHITECTURE.md decision #16) across the ~97-day scrape buffer, so total ingestion task duration scales with `SCRAPE_BUFFER_DAYS` per origin, not with the number of destinations — this should factor into the Airflow DAG's schedule interval and task timeout
- Docker Engine must be installed and running locally to develop or run the pipeline — every task container is built and launched through it
- Silver, as a BigQuery external table, carries real limitations vs. a native table (no clustering, some DML restrictions) — acceptable since `dbt run` only reads it once per scheduled run, not queried repeatedly by end users

## Open Questions

- [ ] No refresh/backfill mechanism exists: regenerating silver history or backfilling bronze has no defined procedure for rebuilding gold (which tables, in what order, and how stateful ones are carried across) — needs an explicit runbook or automation
- [ ] Dashboard analysis/design: what exact metrics, breakdowns, and visualizations does the dashboard need? Blocks the exact gold star schema (see `pipeline/transform/`)
- [ ] Looker Studio vs. Tableau Public: is Looker Studio's visualization depth sufficient, or does the analysis need richer charting/interactivity that would justify the Tableau + Sheets-export trade-off instead?
- [ ] Data quality checks: a CI smoke test catches crash-level bugs in the ingestion image, but content-level validation (price bounds, null checks, anomaly detection) is still untouched, pending the dbt/gold layer
- [ ] Logging: what's the logging strategy across containers — structured logs, centralized collection, or just each container's stdout captured by Airflow?
- [ ] Failure management: `ingest_airport`/`scrape_ryanair` currently produce several distinct partial-failure shapes, each recovered differently today — is there a more elegant, unified way to handle them?
  1. Scrape returns empty (every date failed) — every date gets recorded to the retry queue; `ingest_airport` returns `False`
  2. Scrape returns a partial result (some dates failed) — only the failed dates get recorded to the retry queue; `ingest_airport` still returns `True`
  3. The scrape process itself fails (crash/timeout) after recording some dates to the retry queue but before returning — partial retry-queue state, task fails
  4. The scrape process itself fails before recording anything to the retry queue — task fails, no retry-queue state at all, that run's failed dates are unrecoverable
- [ ] Ambiguous/unknown airport discovery doesn't survive the containerized pipeline: `AMBIGUOUS_AIRPORTS_PATH`/`UNKNOWN_AIRPORTS_PATH` (`config.py`) are local-filesystem-only paths, with no GCS-aware equivalent like the flight cache/retry queue have. Inside `ingest_flights`/`retry_failed_ingests` containers, a discovery is written to that container's own ephemeral filesystem and destroyed with it (`auto_remove="force"`, no volume mount back to the host) — it never reaches the git-tracked `src/` files for review. Even with a mount, concurrent containers (`max_active_tis_per_dagrun=5`) would race on a plain `open(path, "w")` write with no CAS protection, unlike the cache/retry-queue writes. Only the v1 CLI / `manual_run.py`, run directly on a developer's machine, actually persist discoveries today.
- [ ] No dead-letter concept for the retry queue: a permanently-failing query retries forever with no signal it's stuck

---

## Decision Log (ADR summary)

| ADR | Decision                                                                                          | Status   |
| --- | ------------------------------------------------------------------------------------------------- | -------- |
| 001 | v2 documented in a separate file (`ARCHITECTURE_DASHBOARD.md`), own ADR numbering                 | Accepted |
| 002 | Terraform provisions the GCS bucket and service account                                           | Accepted |
| 003 | Developer impersonates the service account instead of using a downloaded key                      | Accepted |
| 004 | Container-per-task topology for the pipeline                                                      | Accepted |
| 005 | Apache Airflow for orchestration, self-hosted via Docker Compose                                  | Accepted |
| 006 | Airflow runs locally via Docker Compose                                                           | Accepted |
| 007 | v2 pipeline reuses v1's scraper/cache (`src/`) unchanged, no duplicate scraper                    | Accepted |
| 008 | Scrape-origins list stays a hardcoded Python constant — GCS-hosted version tried, but Airflow also need scrape-origins list to trigger tasks and this would force `google-cloud-storage` into the Airflow image (decision #004 dependency conflict) | Reverted |
| 009 | GCS for bronze/silver landing (GCP over AWS/Azure)                                                | Accepted |
| 010 | Flights partitioned daily per airline/origin                                                      | Accepted |
| 011 | v1 CLI checks GCS before scraping; local cache is fallback-only for when GCS is unreachable       | Accepted |
| 012 | Pipeline skips an origin already scraped same-day in GCS, avoiding duplicate scraping vs. the CLI | Accepted |
| 013 | Bronze not loaded into BigQuery — only silver/gold reach it                                       | Accepted |
| 014 | Local PySpark (`local[*]`), replacing Databricks Community Edition                                | Accepted |
| 015 | Silver stays in GCS as Parquet, not written directly to BigQuery                                  | Accepted |
| 016 | BigQuery as the warehouse                                                                         | Accepted |
| 017 | Silver exposed to BigQuery as an external table, not loaded                                       | Accepted |
| 018 | dbt Core (not dbt Cloud) for transformation                                                       | Accepted |
| 019 | Looker Studio dashboard, connected directly to BigQuery                                           | Accepted |
| 020 | CI builds/pushes the ingestion image to GHCR, but local Docker Compose/Airflow keep building `:dev` locally until v2 is finished | Accepted |
| 021 | DagRun-failure alerting logs one line; a separate task handles per-origin/aggregate reporting     | Accepted |
| 022 | `flight_key` = route-day grain (origin, destination, departure date, airline) — one key per route-day's cheapest fare, shared by silver and the ingestion retry-merge; physical-flight key replaced | Accepted |
