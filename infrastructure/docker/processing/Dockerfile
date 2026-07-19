FROM python:3.12-slim

# PySpark needs a JVM; procps supplies `ps` for Spark's launcher scripts.
RUN apt-get update && apt-get install --no-install-recommends -y default-jre-headless procps curl \
    && rm -rf /var/lib/apt/lists/*

# Keep versions in sync with pyproject.toml's [project.dependencies].
RUN pip install --no-cache-dir "pyspark>=3.5.0,<4.0"

# The -shaded gcs-connector jar, baked in so containers never download at runtime — the plain
# Ivy coordinate resolves the unshaded jar, which collides with Spark's own Guava/Protobuf
# (see run_silver.py's _GCS_CONNECTOR_JAR comment).
RUN mkdir -p /opt/spark-jars && curl -fsSL -o /opt/spark-jars/gcs-connector-hadoop3-2.2.28-shaded.jar \
    https://repo1.maven.org/maven2/com/google/cloud/bigdataoss/gcs-connector/hadoop3-2.2.28/gcs-connector-hadoop3-2.2.28-shaded.jar
ENV GCS_CONNECTOR_JAR_PATH=/opt/spark-jars/gcs-connector-hadoop3-2.2.28-shaded.jar

COPY src/ /app/src/
COPY pipeline/processing/ /app/pipeline/processing/

ENV PYTHONPATH="/app/src:/app/pipeline/processing"
WORKDIR /app

ENTRYPOINT ["python", "pipeline/processing/run_silver.py"]
