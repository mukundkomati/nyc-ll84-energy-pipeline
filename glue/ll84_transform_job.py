"""AWS Glue job: raw LL84 CSV in S3 -> curated Parquet tables + EUI flags in S3.

Runs on Glue 5.0. The transformation itself is pandas (the `ll84` package, installed into
the job via --additional-python-modules from the wheel uploaded by infra/deploy_glue.sh).
LL84 is ~100K rows, so a single pandas process is faster and cheaper than distributing it.

Job parameters:
  --RAW_URI        s3://bucket/raw/ll84            (latest ingest_date partition is used)
  --CURATED_URI    s3://bucket/curated/ll84
  --RUN_FLAGS      true|false                       (default true)
  --YOY_PCT_THRESHOLD, --PEER_Z_THRESHOLD, --IQR_MULTIPLIER, --MIN_PEER_GROUP (optional)
"""

import json
import logging
import sys

from awsglue.utils import getResolvedOptions

from ll84.flagging import FlagConfig
from ll84.logging_setup import configure
from ll84.pipeline import run_transform

REQUIRED = ["JOB_NAME", "RAW_URI", "CURATED_URI"]
OPTIONAL = {
    "RUN_FLAGS": "true",
    "YOY_PCT_THRESHOLD": "0.5",
    "PEER_Z_THRESHOLD": "3.5",
    "IQR_MULTIPLIER": "3.0",
    "MIN_PEER_GROUP": "20",
}


def resolve_args(argv):
    present = [k for k in OPTIONAL if f"--{k}" in argv]
    args = getResolvedOptions(argv, REQUIRED + present)
    return {**OPTIONAL, **args}


def main():
    configure(log_dir=None)  # Glue ships stdout to CloudWatch
    log = logging.getLogger("ll84.glue")
    args = resolve_args(sys.argv)
    flag_cfg = None
    if args["RUN_FLAGS"].lower() == "true":
        flag_cfg = FlagConfig(
            pct_threshold=float(args["YOY_PCT_THRESHOLD"]),
            z_threshold=float(args["PEER_Z_THRESHOLD"]),
            iqr_multiplier=float(args["IQR_MULTIPLIER"]),
            min_peer_group=int(args["MIN_PEER_GROUP"]),
        )
    log.info(
        "Starting %s raw=%s curated=%s", args["JOB_NAME"], args["RAW_URI"], args["CURATED_URI"]
    )
    report = run_transform(args["RAW_URI"], args["CURATED_URI"], flag_cfg)
    log.info("QUALITY_REPORT %s", json.dumps(report, default=str))


if __name__ == "__main__":
    main()
