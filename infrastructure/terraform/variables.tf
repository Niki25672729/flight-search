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
