#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# .env is the single source of truth for values shared across Terraform/dbt/docker-compose
# (see infrastructure/terraform/tf.sh for Terraform's side of this).
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  source "$REPO_ROOT/.env"
  set +a
fi

# ---------------------------
# Env check
# ---------------------------

if [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  echo "GOOGLE_CLOUD_PROJECT is not set — required by profiles.yml, even for 'dbt parse'/'dbt compile'." >&2
  exit 1
fi

if [ -z "${BIGQUERY_DATASET:-}" ]; then
  echo "BIGQUERY_DATASET is not set — required by profiles.yml/_sources.yml, even for 'dbt parse'/'dbt compile'." >&2
  exit 1
fi

# ---------------------------
# Regenerate seed_airports.csv (src/eu_airports.json is the single source of truth)
# ---------------------------

python3 -c "
import csv
import json
import os

with open('$REPO_ROOT/src/eu_airports.json') as f:
    airports = json.load(f)

os.makedirs('$REPO_ROOT/pipeline/transform/seeds', exist_ok=True)
with open('$REPO_ROOT/pipeline/transform/seeds/seed_airports.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['airport_iata', 'city', 'country'])
    for iata, info in sorted(airports.items()):
        writer.writerow([iata, info['city'], info['country']])
"

# ---------------------------
# dbt parse/compile (local testing stays capped here — ARCHITECTURE_DASHBOARD.md decision #023)
# ---------------------------

# run_date's actual value doesn't matter for parse/compile (no query ever executes) — just needs
# to be present, since stg_flights_latest_state/stg_flight_price_history require it with no default.
RUN_DATE_VARS="{\"run_date\": \"$(date +%Y%m%d)\"}"

dbt parse --project-dir "$SCRIPT_DIR" --profiles-dir "$SCRIPT_DIR" --vars "$RUN_DATE_VARS"
dbt compile --project-dir "$SCRIPT_DIR" --profiles-dir "$SCRIPT_DIR" --vars "$RUN_DATE_VARS"
