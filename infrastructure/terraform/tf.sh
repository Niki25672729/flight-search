#!/bin/bash
set -euo pipefail

# Single source of truth for values shared between Terraform and the app/dbt side: .env holds the
# real value, this wrapper maps it to the TF_VAR_ name Terraform expects. Add one line here per
# variable moved onto this pattern. Note: a literal value in terraform.tfvars always overrides a
# TF_VAR_* env var, so a variable's tfvars line must be removed for its mapping here to take effect.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  source "$REPO_ROOT/.env"
  set +a
fi

if [ -n "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  export TF_VAR_project_id="$GOOGLE_CLOUD_PROJECT"
fi

if [ -n "${FLIGHT_SEARCH_GCS_BUCKET:-}" ]; then
  export TF_VAR_bucket_name="$FLIGHT_SEARCH_GCS_BUCKET"
fi

if [ -n "${BRONZE_GCS_PREFIX:-}" ]; then
  export TF_VAR_bronze_gcs_prefix="$BRONZE_GCS_PREFIX"
fi

if [ -n "${SILVER_GCS_PREFIX:-}" ]; then
  export TF_VAR_silver_gcs_prefix="$SILVER_GCS_PREFIX"
fi

if [ -n "${BIGQUERY_DATASET:-}" ]; then
  export TF_VAR_bigquery_dataset_id="$BIGQUERY_DATASET"
fi

# Enforcement gate — variables.tf's via_tf_sh validation fails loudly if this isn't "yes",
# catching anyone who calls `terraform` directly and skips the .env sourcing above.
export TF_VAR_via_tf_sh="yes"

exec terraform -chdir="$SCRIPT_DIR" "$@"
