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
|----------------|------------------------------------------------------------------|
| Responsibility | Provision shared GCP resources: the bronze/silver GCS bucket (90-day lifecycle rule on `bronze/`) and a service account with `storage.objectAdmin`, granted via impersonation rather than a downloaded key |
| Inputs         | Terraform variables (`terraform.tfvars`, gitignored)             |
| Outputs        | GCS bucket + service account that the CLI (`src/cache.py`) and pipeline (`pipeline/ingestion/core.py`) authenticate against |
| Key files      | `infrastructure/terraform/main.tf`, `variables.tf`, `outputs.tf` |
| External calls | GCP Storage + IAM APIs, via the `hashicorp/google` provider      |

### `infrastructure/docker/`

| Field          | Value                                                                              |
|----------------|------------------------------------------------------------------------------------|
| Responsibility | Dockerfiles for each per-task container: Airflow, ingestion, processing, transform |
| Inputs         | N/A (build-time definitions)                                                       |
| Outputs        | Container images that Airflow launches via `DockerOperator`                        |
| Key files      | `infrastructure/docker/{task}/Dockerfile`                                          |
| External calls | None (local Docker builds)                                                         |

### `infrastructure/airflow/` (TBD)

| Field          | Value                                                                     |
|----------------|---------------------------------------------------------------------------|
| Responsibility | Define the DAG (ingest → processing → transform) and run it on a schedule |
| Inputs         | None (time-triggered)                                                     |
| Outputs        | Pipeline run logs, task success/failure status, retries                   |
| Key files      | `infrastructure/airflow/dags/flight_pipeline_dag.py`                      |
| External calls | Docker Engine (via `DockerOperator`)                                      |

### `pipeline/ingestion/`

| Field          | Value                                                                                            |
|----------------|--------------------------------------------------------------------------------------------------|
| Responsibility | `ingest_airport` calls v1's scraper (`src/scraper.py`) per configured airport and writes bronze to GCS<br>`retry_failed_ingests` re-attempts previously failed {origin, date} pairs from the retry queue and merges recovered flights |
| Inputs         | Scrape-origins file on GCS                                                                       |
| Outputs        | Raw availability data written to GCS as NDJSON. Flights are partitioned daily as `bronze/flights/{airline}/{origin}/{YYYYMM}/{YYYYMMDD}.json`. |
| Key files      | `pipeline/ingestion/core.py`, `pipeline/ingestion/manual_run.py` (manual/hotfix CLI entry point) |
| External calls | `scraper` (v1's `src/scraper.py`, imported directly), Google Cloud Storage                       |

### `pipeline/processing/` (TBD)

| Field          | Value                                                                     |
|----------------|---------------------------------------------------------------------------|
| Responsibility | PySpark job: read bronze flights, clean/dedupe/type, write silver         |
| Inputs         | Bronze GCS path (flights)                                                 |
| Outputs        | Silver (Parquet) written to GCS, exposed to BigQuery as an external table |
| Key files      | TBD                                                                       |
| External calls | Google Cloud Storage                                                      |

### `pipeline/transform/` (TBD)

| Field          | Value                                                              |
|----------------|--------------------------------------------------------------------|
| Responsibility | Model silver → gold star schema (exact fact/dimension tables TBD, pending dashboard requirements); tests and docs |
| Inputs         | Silver flights, exposed as a BigQuery external table over GCS      |
| Outputs        | Gold tables in BigQuery (native), dbt docs site, test results      |
| Key files      | `pipeline/transform/models/`, `pipeline/transform/dbt_project.yml` |
| External calls | BigQuery (via `dbt-bigquery` adapter)                              |

### `dashboards/looker/` (TBD)

| Field          | Value                                                                                       |
|----------------|---------------------------------------------------------------------------------------------|
| Responsibility | Visualize gold tables — price trends by destination, cheapest destinations, price over time |
| Inputs         | Gold tables in BigQuery (native connector, live or scheduled refresh)                       |
| Outputs        | Looker Studio report, shareable link (public or restricted)                                 |
| Key files      | N/A (cloud-native report, no local project file)                                            |
| External calls | BigQuery                                                                                    |

## Data Flow

1. Airflow DAG triggers on schedule (e.g. daily)
2. Task 1 — ingestion (`pipeline/ingestion/core.py`'s `ingest_airport`) calls v1's scraper for each configured airport and writes bronze to GCS; `retry_failed_ingests` re-attempts previously failed {origin, date} pairs from the retry queue
3. Task 2 — PySpark job (`pipeline/processing/`) reads bronze flights, cleans/dedupes/types, writes silver (Parquet) to GCS
4. Task 3 — `dbt run` (`pipeline/transform/`, via `dbt-bigquery`) queries silver through a BigQuery external table over the GCS path (a lightweight pointer, not a load job) and materializes gold star schema natively in BigQuery (exact schema TBD, pending dashboard requirements); `dbt test` validates it
5. Looker Studio (`dashboards/looker/`) connects directly to the gold BigQuery tables (native connector, live or scheduled refresh) — no separate export step needed

### Shared GCS Cache Convention

v1 and v2 read/write the same GCS bronze paths, but each with different intent:

- **v1 CLI** (see [ARCHITECTURE.md](./ARCHITECTURE.md)): checks GCS for a fresh flights blob before scraping (existing 1-day TTL, just checked against GCS instead of local disk first); falls back to its local `cache/` folder only if GCS itself is unreachable, never on a normal miss
- **Pipeline** (`pipeline/ingestion/`): before scraping an origin on its scheduled run, checks whether *today's exact-date* blob already exists in GCS for that origin and skips scraping it again if so.

This lets an on-demand CLI search and the scheduled pipeline share scrape cost instead of duplicating it, while keeping each system's failure mode independent: the CLI never needs GCS to function, and the pipeline never depends on the CLI having been run.

## Key Design Decisions

| #   | Decision                                                                                             | Alternatives considered                                                | Rationale                                                       |
|-----|------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|-----------------------------------------------------------------|
| 001 | v2 lives in the same repo as v1, as an additive `pipeline/`, `dashboards/`, and `infrastructure/` layer, documented in a separate file | Single combined ARCHITECTURE.md; separate repository                   | Shares `src/` and repo-wide conventions (CLAUDE.md, CI, dependency management) with v1 instead of duplicating them across two repos, while separate doc files (`ARCHITECTURE.md` vs `ARCHITECTURE_PIPELINE.md`) keep each system's design reasoning independently readable without cross-referencing every decision |
| 002 | Terraform provisions the GCS bucket and service account (`infrastructure/terraform/`)                | Manual setup via `gcloud`/console                                      | Reproducible, versioned, self-documenting infra; matches the free-tier setup exactly and can be torn down/recreated without manual steps |
| 003 | Developer impersonates the service account (`roles/iam.serviceAccountTokenCreator`) rather than a downloaded key | Downloaded service account key (`.json`)                             | Org policy blocks key creation; impersonation avoids a long-lived credential file to protect entirely |
| 004 | Each pipeline task (ingestion, processing, transform) runs in its own container                      | One container for everything; isolate every task uniformly ("one process per container" as a blanket rule) | Isolation is warranted by a real discriminator — a different runtime (PySpark needs a JVM) or a dependency-conflict risk with Airflow's own pinned packages (`dbt-core`, `ryanair-py`/`google-cloud-storage` version pins) — not by isolating for its own sake |
| 005 | Apache Airflow for orchestration                                                                     | GitHub Actions, Databricks Workflows, dbt Cloud scheduler              | Airflow gives task-level retries, backfills, and dependency-aware scheduling needed to run ingest → processing → transform as one DAG with per-task state; GitHub Actions is a CI/CD tool without native backfill/catchup, and the dbt Cloud scheduler only covers the dbt step, not the whole pipeline. Databricks Workflows isn't available on Community Edition |
| 006 | Airflow runs locally via Docker Compose                                                              | Always-on Cloud Composer                                               | Always-on managed Airflow isn't free; local Docker Compose is free forever and sufficient to develop and demo the DAG |
| 007 | Reuse v1's scraper/cache (`src/`) as pipeline ingestion source                                       | Separate scraper implementation for pipeline                           | One source of truth; avoids duplicated scraping logic and drift |
| 008 | Scrape-origins list stored in a config file on GCS, not hardcoded in `src/config.py`                 | Hardcoded Python constant; Airflow Variable                            | Editable without a code change or redeploy — ops can add/remove origins by updating the GCS file directly; keeping it in GCS (not an Airflow Variable) stays consistent with this pipeline's existing GCS-centric config/data conventions rather than coupling it to Airflow's own metadata store |
| 009 | GCS for bronze/silver landing                                                                        | S3, Azure Blob                                                         | Same-cloud integration with BigQuery (native external-table support over GCS, no cross-cloud auth or egress); free tier covers current data volume |
| 010 | v1 CLI checks GCS before scraping; local cache is fallback-only, used when GCS itself is unreachable | Keep v1 and v2 caches fully separate (original design)                 | Sharing a single GCS cache lets an on-demand CLI search and the scheduled pipeline avoid duplicating scrape cost, while local cache still guarantees v1 works with no GCP access at all |
| 011 | Pipeline skips an origin if today's exact-date GCS blob for it already exists                        | Always re-scrape every origin on every scheduled run                   | Avoids duplicate scraping when the CLI has already triggered an on-demand scrape for that origin earlier the same day |
| 012 | Bronze is not loaded into or exposed in BigQuery — only silver (external table) and gold (native) reach it | Expose raw bronze in BigQuery (e.g. a `raw_flights` table)             | Nothing in the pipeline needs it — Processing reads bronze directly from GCS; would just be an unused warehouse surface with no consumer today |
| 013 | Local PySpark (`local[*]` mode)                                                                      | Databricks Community Edition, Dataproc                                 | No external account/cluster dependency and nothing to spin up; at this project's data volume (single-digit GB, daily batch) there's no functional need for a managed cluster (containerized separately from Airflow per decision #4) |
| 014 | Silver stays in GCS as Parquet, not written directly to BigQuery                                     | PySpark writes directly to BigQuery via the `spark-bigquery-connector` | The connector stages writes through GCS internally anyway, so it doesn't remove the GCS dependency — just adds one more piece of infrastructure. Also keeps a consistent bronze+silver-in-the-lake / gold-in-the-warehouse medallion pattern, with silver durable and warehouse-independent |
| 015 | BigQuery as the warehouse                                                                            | Databricks SQL warehouse, Snowflake                                    | Serverless (no cluster sizing), columnar storage suited to the star-schema query pattern here, and native `dbt-bigquery` + GCS integration within the same GCP project/IAM boundary already used elsewhere; free tier persists indefinitely, unlike Snowflake's time-boxed trial which would force a later migration |
| 016 | Silver is exposed to BigQuery as an external table over GCS, not loaded into a native table          | Native GCS → BigQuery load job before each `dbt run`                   | An external table is a near-free DDL pointer, not a data-moving job; `dbt run` already does the real read-and-transform work when it queries the external table and materializes gold, so a separate load step would just be redundant data movement |
| 017 | dbt Core (not dbt Cloud)                                                                             | dbt Cloud free tier                                                    | No login/seat limits; Airflow already handles scheduling for the whole pipeline, so dbt Cloud's built-in scheduler isn't needed |
| 018 | Looker Studio dashboard, connected directly to BigQuery                                              | Tableau Public (+ Sheets export), Power BI Desktop                     | Native BigQuery connector needs no export/bridge step, unlike Tableau Public which can't connect to BigQuery directly; free, and its sharing model supports both public links and restricted access, unlike Tableau Public (public-only) or Power BI Desktop (no live link without a paid Service license) |

## External Dependencies

**Active:**

| Name                      | Purpose                                            | Docs                                                                 |
|---------------------------|----------------------------------------------------|----------------------------------------------------------------------|
| terraform                 | IaC tool (provisions GCS bucket + service account) | https://developer.hashicorp.com/terraform                            |
| terraform-provider-google | GCP provider plugin for Terraform                  | https://registry.terraform.io/providers/hashicorp/google/latest/docs |
| google-cloud-storage      | Read/write bronze and silver data to GCS           | https://cloud.google.com/python/docs/reference/storage/latest        |

**Planned, not yet active:**

| Name                            | Purpose                                                        | Docs                                                                              |
|---------------------------------|----------------------------------------------------------------|-----------------------------------------------------------------------------------|
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

- [ ] Dashboard analysis/design: what exact metrics, breakdowns, and visualizations does the dashboard need? Blocks the exact gold star schema (see `pipeline/transform/`)
- [ ] Looker Studio vs. Tableau Public: is Looker Studio's visualization depth sufficient, or does the analysis need richer charting/interactivity that would justify the Tableau + Sheets-export trade-off instead?
- [ ] Data quality checks: beyond `dbt test` on gold, does bronze/silver need its own validation (row counts, null checks, schema drift) before promoting to the next layer?
- [ ] Monitoring/alerting: how are task failures, data staleness, or the `status.json` scrape-task summary surfaced — Airflow's own UI only, or something more (email/Slack alert)?
- [ ] Logging: what's the logging strategy across containers — structured logs, centralized collection, or just each container's stdout captured by Airflow?

---

## Decision Log (ADR summary)

| ADR | Decision                                                                                          | Status   |
|-----|---------------------------------------------------------------------------------------------------|----------|
| 001 | v2 documented in a separate file (`ARCHITECTURE_PIPELINE.md`), own ADR numbering                  | Accepted |
| 002 | Terraform provisions the GCS bucket and service account                                           | Accepted |
| 003 | Developer impersonates the service account instead of using a downloaded key                      | Accepted |
| 004 | Container-per-task topology for the pipeline                                                      | Accepted |
| 005 | Apache Airflow for orchestration, self-hosted via Docker Compose                                  | Accepted |
| 006 | Airflow runs locally via Docker Compose                                                           | Accepted |
| 007 | v2 pipeline reuses v1's scraper/cache (`src/`) unchanged, no duplicate scraper                    | Accepted |
| 008 | Scrape-origins list stored in a config file on GCS, not hardcoded                                 | Accepted |
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
| 019 | Looker Studio dashboard, connected directly to BigQuery, replacing Power BI                       | Accepted |