resource "google_project_service" "bigquery" {
  project            = var.project_id
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

# Dataset for both silver external tables (below) and gold tables (dbt, pipeline/transform/)
resource "google_bigquery_dataset" "flight_search" {
  project     = var.project_id
  dataset_id  = var.bigquery_dataset_id
  location    = var.region
  description = "Silver external tables (over GCS) and gold tables (dbt-materialized) for the flight-search v2 pipeline."

  depends_on = [google_project_service.bigquery]
}

# For creating/replacing gold tables and running queries, reuse the ingestion service account instead of minting a second one
resource "google_bigquery_dataset_iam_member" "flight_search_storage_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.flight_search.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.flight_search_storage.email}"
}

resource "google_project_iam_member" "flight_search_storage_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.flight_search_storage.email}"
}

# ---------------------------
# Silver external tables
# ---------------------------
#
# Lightweight DDL pointers over GCS Parquet, not loaded
# — dbt's staging models (pipeline/transform/models/staging/) read straight through these.
#
# Schemas are declared explicitly, matching bronze_reader.py's "explicit schema, not inferSchema"
# convention (a silently-wrong autodetected type is worse than a loud failure) and field-for-field
# mirroring pipeline/processing/silver.py's actual output columns.
locals {
  flights_latest_state_schema = jsonencode([
    { name = "origin_iata", type = "STRING", mode = "REQUIRED" },
    { name = "destination_iata", type = "STRING", mode = "REQUIRED" },
    { name = "destination_city", type = "STRING", mode = "REQUIRED" },
    { name = "destination_country", type = "STRING", mode = "REQUIRED" },
    { name = "airline", type = "STRING", mode = "REQUIRED" },
    { name = "flight_number", type = "STRING", mode = "NULLABLE" },
    { name = "departure_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "arrival_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "price_eur", type = "FLOAT64", mode = "REQUIRED" },
    { name = "currency", type = "STRING", mode = "REQUIRED" },
    { name = "seats_left", type = "INT64", mode = "NULLABLE" },
    { name = "scraped_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "flight_key", type = "STRING", mode = "REQUIRED" },
  ])

  # airline added post-launch, ahead of multi-airline scraping (pipeline/processing/silver.py) —
  # only Ryanair exists today (src/config.py), but the column lets a future 2nd airline's rows be
  # distinguished instead of silently blending into this.
  flight_price_history_schema = jsonencode([
    { name = "flight_key", type = "STRING", mode = "REQUIRED" },
    { name = "origin_iata", type = "STRING", mode = "REQUIRED" },
    { name = "destination_iata", type = "STRING", mode = "REQUIRED" },
    { name = "airline", type = "STRING", mode = "REQUIRED" },
    { name = "flight_number", type = "STRING", mode = "NULLABLE" },
    { name = "departure_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "price_eur", type = "FLOAT64", mode = "REQUIRED" },
    { name = "prior_price_eur", type = "FLOAT64", mode = "NULLABLE" },
    { name = "is_new_flight", type = "BOOLEAN", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "flights_latest_state" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.flight_search.dataset_id
  table_id            = "flights_latest_state_external"
  deletion_protection = false

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${var.bucket_name}/${var.silver_gcs_prefix}/flights_latest_state/scrape_date=*"]
    schema        = local.flights_latest_state_schema

    hive_partitioning_options {
      mode                     = "AUTO"
      source_uri_prefix        = "gs://${var.bucket_name}/${var.silver_gcs_prefix}/flights_latest_state/"
      require_partition_filter = true
    }
  }
}

resource "google_bigquery_table" "flight_price_history" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.flight_search.dataset_id
  table_id            = "flight_price_history_external"
  deletion_protection = false

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${var.bucket_name}/${var.silver_gcs_prefix}/flight_price_history/scrape_date=*"]
    schema        = local.flight_price_history_schema

    hive_partitioning_options {
      mode                     = "AUTO"
      source_uri_prefix        = "gs://${var.bucket_name}/${var.silver_gcs_prefix}/flight_price_history/"
      require_partition_filter = true
    }
  }
}
