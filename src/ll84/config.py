"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"} and value[:1] in value[1:]:
            value = value[1 : value.index(value[0], 1)]
        else:
            value = "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), value)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Source dataset (NYC Open Data / Socrata)
    dataset_id: str
    socrata_domain: str
    socrata_app_token: Optional[str]
    # AWS
    aws_region: str
    s3_bucket: Optional[str]
    raw_prefix: str
    curated_prefix: str
    glue_database: str
    glue_job_name: str
    athena_workgroup: str
    # Postgres
    database_url: Optional[str]
    # Local fallback (used when S3_BUCKET is not set)
    local_data_dir: Path
    log_dir: Path
    # Flagging thresholds
    yoy_pct_threshold: float
    peer_z_threshold: float
    iqr_multiplier: float
    min_peer_group: int

    @property
    def raw_uri(self) -> str:
        if self.s3_bucket:
            return f"s3://{self.s3_bucket}/{self.raw_prefix.strip('/')}"
        return str(self.local_data_dir / "raw")

    @property
    def curated_uri(self) -> str:
        if self.s3_bucket:
            return f"s3://{self.s3_bucket}/{self.curated_prefix.strip('/')}"
        return str(self.local_data_dir / "curated")


def get_settings(env_file: str | os.PathLike = ".env") -> Settings:
    load_dotenv(env_file)
    return Settings(
        dataset_id=_env("LL84_DATASET_ID", "5zyy-y8am"),
        socrata_domain=_env("SOCRATA_DOMAIN", "data.cityofnewyork.us"),
        socrata_app_token=_env("SOCRATA_APP_TOKEN"),
        aws_region=_env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")),
        s3_bucket=_env("S3_BUCKET"),
        raw_prefix=_env("S3_RAW_PREFIX", "raw/ll84"),
        curated_prefix=_env("S3_CURATED_PREFIX", "curated/ll84"),
        glue_database=_env("GLUE_DATABASE", "ll84"),
        glue_job_name=_env("GLUE_JOB_NAME", "ll84-transform"),
        athena_workgroup=_env("ATHENA_WORKGROUP", "ll84-wg"),
        database_url=_env("DATABASE_URL"),
        local_data_dir=Path(_env("LOCAL_DATA_DIR", "data")),
        log_dir=Path(_env("LL84_LOG_DIR", "logs")),
        yoy_pct_threshold=float(_env("YOY_PCT_THRESHOLD", "0.5")),
        peer_z_threshold=float(_env("PEER_Z_THRESHOLD", "3.5")),
        iqr_multiplier=float(_env("IQR_MULTIPLIER", "3.0")),
        min_peer_group=int(_env("MIN_PEER_GROUP", "20")),
    )
