variable "project_id" {
  description = "GCP project ID"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be 6-30 characters: lowercase letters, digits, hyphens; starting with a letter and not ending with a hyphen."
  }
}

variable "region" {
  description = "GCS bucket location"
  type        = string
  default     = "US-CENTRAL1"
}

variable "bucket_name" {
  description = "GCS bucket name for all GCS-resident data layers (bronze/silver), organized by folder prefix"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3-63 characters: lowercase letters, digits, hyphens, underscores, or dots; starting and ending with a letter or digit."
  }
}

variable "service_account_id" {
  description = "Service account ID (without the @project.iam.gserviceaccount.com suffix)"
  type        = string
  default     = "flight-search-storage-admin"
}

variable "impersonator_email" {
  description = "User email allowed to impersonate the service account for local development"
  type        = string
}

variable "bronze_gcs_prefix" {
  description = "GCS prefix (under bucket_name) bronze is written to"
  type        = string
}


variable "silver_gcs_prefix" {
  description = "GCS prefix (under bucket_name) the silver external tables read from"
  type        = string
}

variable "bigquery_dataset_id" {
  description = "BigQuery dataset holding the silver external tables and gold (dbt) tables"
  type        = string
}

# Internal sentinel — never set this by hand. tf.sh exports TF_VAR_via_tf_sh=yes so that a plain
# `terraform` invocation (which skips tf.sh's .env sourcing for project_id, etc) fails loudly
# here instead of silently using stale or missing values.
variable "via_tf_sh" {
  description = "Internal sentinel — do not set manually. Enforces that Terraform is only run through tf.sh."
  type        = string
  default     = ""

  validation {
    condition     = var.via_tf_sh == "yes"
    error_message = "Run Terraform via ./infrastructure/terraform/tf.sh, not `terraform` directly — it sources .env for project_id, bucket_name, bronze_gcs_prefix, and bigquery_dataset_id."
  }
}