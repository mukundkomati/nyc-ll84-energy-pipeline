"""Start the Glue job from the CLI and wait for it to finish."""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

log = logging.getLogger(__name__)

TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR", "EXPIRED"}


def run_glue_job(
    job_name: str,
    arguments: Optional[Dict[str, str]] = None,
    region: Optional[str] = None,
    poll_seconds: int = 20,
    client=None,
) -> str:
    import boto3

    glue = client or boto3.client("glue", region_name=region)
    run_id = glue.start_job_run(JobName=job_name, Arguments=arguments or {})["JobRunId"]
    log.info("Started Glue job %s run %s", job_name, run_id)
    while True:
        run = glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        state = run["JobRunState"]
        if state in TERMINAL:
            break
        log.info("Glue job %s: %s (%ss elapsed)", job_name, state, run.get("ExecutionTime", 0))
        time.sleep(poll_seconds)
    if state != "SUCCEEDED":
        raise RuntimeError(
            f"Glue job {job_name} run {run_id} ended {state}: {run.get('ErrorMessage', '')}"
        )
    log.info("Glue job %s run %s succeeded in %ss", job_name, run_id, run.get("ExecutionTime"))
    return run_id
