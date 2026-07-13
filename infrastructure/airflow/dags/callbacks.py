import logging
from typing import Any


# ---------------------------
# Public API
# ---------------------------


def log_dagrun_failure_alert(context: dict[str, Any]) -> None:
    """
    DAG-level on_failure_callback: fires once per failed DagRun.
    Emits one ERROR-level, PIPELINE_ALERT-prefixed log line naming every failed task.

    No email — deliberate minimum signal for now (MONITORING.md priority 1). Real email
    alerting needs an SMTP service added to docker-compose.yml; that's a separate decision.

    Only fires on real DagRun failure — not on anything generate_run_report observes,
    which stays purely informational (see report.py).
    """
    dag_run = context["dag_run"]
    failed_tasks = [
        f"{ti.task_id}[{ti.map_index}]" if ti.map_index != -1 else ti.task_id
        for ti in dag_run.get_task_instances(state="failed")
    ]
    logging.error("PIPELINE_ALERT dag_id=%s run_id=%s failed_tasks=%s", dag_run.dag_id, dag_run.run_id, failed_tasks)
