from pyspark.sql import SparkSession


# ---------------------------
# Public API
# ---------------------------


def path_exists(spark: SparkSession, path: str) -> bool:
    """
    Checks whether `path` exists via Spark's own Hadoop FileSystem, not Python's os.path — this
    works uniformly for local paths and gs:// paths (once the GCS connector is configured on the
    SparkSession), so callers don't need a separate cloud-path branch.
    """
    assert spark._jsc is not None and spark._jvm is not None  # always set on a live SparkSession
    hadoop_conf = spark._jsc.hadoopConfiguration()
    jvm_path = spark._jvm.org.apache.hadoop.fs.Path(path)
    fs = jvm_path.getFileSystem(hadoop_conf)
    return fs.exists(jvm_path)


def list_partition_values(spark: SparkSession, path: str, partition_key: str) -> list[str]:
    """
    Lists Hive-style partition values (e.g. "2026-07-07") directly under `path` for a
    `{partition_key}=value` layout, via Hadoop FileSystem so this works for gs:// too. Returns []
    if `path` doesn't exist yet (no partitions written yet).
    """
    assert spark._jsc is not None and spark._jvm is not None
    hadoop_conf = spark._jsc.hadoopConfiguration()
    jvm_path = spark._jvm.org.apache.hadoop.fs.Path(path)
    fs = jvm_path.getFileSystem(hadoop_conf)
    if not fs.exists(jvm_path):
        return []
    prefix = f"{partition_key}="
    values = []
    for status in fs.listStatus(jvm_path):
        if status.isDirectory():
            name = status.getPath().getName()
            if name.startswith(prefix):
                values.append(name[len(prefix) :])
    return values


def peek_first_non_space_char(spark: SparkSession, path: str) -> str | None:
    """First non-whitespace char (None if empty) — tells local JSON-array bronze ("[") from GCS NDJSON ("{")."""
    assert spark._jsc is not None and spark._jvm is not None  # always set on a live SparkSession
    hadoop_conf = spark._jsc.hadoopConfiguration()
    jvm_path = spark._jvm.org.apache.hadoop.fs.Path(path)
    fs = jvm_path.getFileSystem(hadoop_conf)
    stream = fs.open(jvm_path)
    try:
        while True:
            byte = stream.read()
            if byte == -1:
                return None
            char = chr(byte)
            if not char.isspace():
                return char
    finally:
        stream.close()
