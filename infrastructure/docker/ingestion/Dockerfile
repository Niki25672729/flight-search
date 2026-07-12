FROM python:3.12-slim

# Keep versions in sync with pyproject.toml's [project.dependencies].
RUN pip install --no-cache-dir \
    "ryanair-py==3.0.0" \
    "google-cloud-storage>=2.18.0"

COPY src/ /app/src/
COPY pipeline/ingestion/ /app/pipeline/ingestion/

ENV PYTHONPATH="/app/src:/app/pipeline/ingestion"
WORKDIR /app

ENTRYPOINT ["python", "pipeline/ingestion/run.py"]
