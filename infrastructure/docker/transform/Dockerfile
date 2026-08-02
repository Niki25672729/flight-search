# syntax=docker/dockerfile:1.4
FROM python:3.12-slim

# git isn't used at runtime (no packages.yml/dbt deps) but `dbt debug` checks for it regardless —
# installed so that check passes cleanly instead of reporting a misleading failure every run.
RUN apt-get update && apt-get install --no-install-recommends -y git && rm -rf /var/lib/apt/lists/*

# Keep versions in sync with pyproject.toml's [project.optional-dependencies].transform.
RUN pip install --no-cache-dir \
    "dbt-core>=1.8,<2.0" \
    "dbt-bigquery>=1.8,<2.0"

COPY src/eu_airports.json /app/src/eu_airports.json
COPY pipeline/transform/ /app/pipeline/transform/

# seed_airports.csv is generated here from eu_airports.json (the single source of truth).
RUN mkdir -p /app/pipeline/transform/seeds && python3 <<'EOF'
import csv
import json

with open('/app/src/eu_airports.json') as f:
    airports = json.load(f)

with open('/app/pipeline/transform/seeds/seed_airports.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['airport_iata', 'city', 'country'])
    for iata, info in sorted(airports.items()):
        writer.writerow([iata, info['city'], info['country']])
EOF

WORKDIR /app/pipeline/transform
# profiles.yml lives alongside dbt_project.yml in this repo, not the dbt-default ~/.dbt/ — no
# secrets in it, so it ships in the image rather than needing a separate mount.
ENV DBT_PROFILES_DIR=/app/pipeline/transform

ENTRYPOINT ["dbt"]
CMD ["run"]
