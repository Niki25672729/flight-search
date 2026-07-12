FROM apache/airflow:2.10.3-python3.12

RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.3/constraints-3.12.txt" \
    "apache-airflow-providers-docker>=3.14.0"

# The DAG reads SCRAPE_ORIGINS from config.py
COPY src/config.py /opt/airflow/ingestion_config/config.py
ENV PYTHONPATH="/opt/airflow/ingestion_config"
