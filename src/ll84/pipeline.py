"""Pipeline steps shared by the CLI and the AWS Glue job."""

from __future__ import annotations

import io
import logging
from typing import Dict, Optional

import pandas as pd

from ll84 import storage
from ll84.extract import latest_raw_file
from ll84.flagging import FlagConfig, flag_eui_changes, log_flags
from ll84.transform import transform

log = logging.getLogger(__name__)

CURATED_TABLES = {
    # table name -> partition column
    "properties": None,
    "meters": None,
    "consumption": "report_year",
    "annual_metrics": "report_year",
}


def read_raw(raw_uri: str) -> pd.DataFrame:
    source = latest_raw_file(raw_uri)
    log.info("Reading raw extract %s", source)
    return pd.read_csv(
        io.BytesIO(storage.read_bytes(source)), dtype=str, keep_default_na=False, low_memory=False
    )


def run_transform(
    raw_uri: str, curated_uri: str, flag_config: Optional[FlagConfig] = None
) -> Dict[str, object]:
    """raw CSV -> curated Parquet tables (+ EUI flags). Used by Glue and by `ll84 transform`."""
    tables = transform(read_raw(raw_uri))
    for name, partition_col in CURATED_TABLES.items():
        storage.write_table(getattr(tables, name), curated_uri, name, partition_col)
    report: Dict[str, object] = dict(tables.quality)
    if flag_config is not None:
        flags = run_flags(curated_uri, flag_config, metrics=tables.annual_metrics)
        report["flags"] = len(flags)
    return report


def run_flags(
    curated_uri: str, cfg: FlagConfig, metrics: Optional[pd.DataFrame] = None
) -> pd.DataFrame:
    metrics = metrics if metrics is not None else storage.read_table(curated_uri, "annual_metrics")
    flags = flag_eui_changes(metrics, cfg)
    log_flags(flags)
    if len(flags):
        storage.write_table(flags, curated_uri, "eui_flags", "report_year")
    else:
        storage.delete_prefix(storage.join(curated_uri, "eui_flags"))
    return flags


def read_curated(curated_uri: str) -> Dict[str, pd.DataFrame]:
    return {name: storage.read_table(curated_uri, name) for name in CURATED_TABLES}


def read_flags(curated_uri: str) -> pd.DataFrame:
    try:
        return storage.read_table(curated_uri, "eui_flags")
    except FileNotFoundError:
        return pd.DataFrame()
