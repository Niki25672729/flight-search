terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
}

resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iam" {
  project            = var.project_id
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

# Single bucket for all GCS-resident layers (bronze/silver), categorized by folder prefix
# rather than separate buckets — data volume doesn't warrant the extra complexity.
resource "google_storage_bucket" "flight_data" {
  name                        = var.bucket_name
  location                    = var.region
  force_destroy               = false      # if the bucket has objects, `terraform destroy` fails instead of silently deleting data
  uniform_bucket_level_access = true       # bucket-level IAM only, no per-object ACLs
  public_access_prevention    = "enforced" # blocks any public IAM binding on this bucket, even by future mistake

  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["bronze/"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.storage]
}

# Shared by both the CLI cache layer (src/cache.py) and the pipeline ingestion job
# (pipeline/ingestion/core.py) — both call the same read_cache/write_cache functions.
resource "google_service_account" "flight_search_storage" {
  account_id   = var.service_account_id
  display_name = var.service_account_id
  description  = "Read/write/delete access to the flight-search GCS bucket, used by both the CLI cache layer and the pipeline ingestion job."

  depends_on = [google_project_service.iam]
}

resource "google_storage_bucket_iam_member" "flight_search_storage_object_admin" {
  bucket = google_storage_bucket.flight_data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.flight_search_storage.email}"
}

# Lets the developer impersonate the service account locally (via `gcloud auth
# application-default login --impersonate-service-account=...`) instead of a
# downloaded service account key, which org policy blocks from being created.
resource "google_service_account_iam_member" "impersonator_token_creator" {
  service_account_id = google_service_account.flight_search_storage.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:${var.impersonator_email}"
}
