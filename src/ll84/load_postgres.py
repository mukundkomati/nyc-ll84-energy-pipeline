"""Load curated tables into Postgres with COPY + upsert (idempotent re-runs)."""

from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql" / "postgres"

# table -> (columns staged from the DataFrame, primary/unique key)
TABLES: Dict[str, Dict[str, List[str]]] = {
    "properties": {
        "columns": [
            "property_id",
            "property_name",
            "parent_property_id",
            "bbl",
            "bbl_count",
            "bin",
            "address",
            "city",
            "postal_code",
            "borough",
            "primary_property_type",
            "largest_property_use_type",
            "year_built",
            "latitude",
            "longitude",
            "first_report_year",
            "last_report_year",
        ],
        "key": ["property_id"],
    },
    "meters": {
        "columns": [
            "meter_id",
            "property_id",
            "energy_source",
            "native_unit",
            "first_report_year",
            "last_report_year",
        ],
        "key": ["meter_id"],
    },
    "annual_metrics": {
        "columns": [
            "property_id",
            "report_year",
            "primary_property_type",
            "gross_floor_area_ft2",
            "site_eui_kbtu_ft2",
            "wn_site_eui_kbtu_ft2",
            "source_eui_kbtu_ft2",
            "wn_source_eui_kbtu_ft2",
            "site_energy_kbtu",
            "source_energy_kbtu",
            "energy_star_score",
            "ghg_emissions_tco2e",
            "ghg_intensity_kgco2e_ft2",
            "water_use_kgal",
            "occupancy_pct",
            "number_of_buildings",
        ],
        "key": ["property_id", "report_year"],
    },
    "consumption": {
        "columns": ["meter_id", "report_year", "usage_kbtu", "usage_native"],
        "key": ["meter_id", "report_year"],
    },
}
LOAD_ORDER = ["properties", "meters", "annual_metrics", "consumption"]
INT_COLUMNS = {
    "property_id",
    "parent_property_id",
    "bbl_count",
    "year_built",
    "first_report_year",
    "last_report_year",
    "report_year",
    "prior_year",
    "energy_star_score",
    "occupancy_pct",
    "number_of_buildings",
}

REVIEW_COLUMNS = [
    "property_id",
    "report_year",
    "prior_year",
    "eui_metric",
    "property_type",
    "prior_eui",
    "current_eui",
    "pct_change",
    "peer_z_score",
    "peer_low",
    "peer_high",
    "direction",
    "reasons",
    "severity",
    "last_flagged_at",
]
# Measurements refreshed on every run; status/reviewer/notes are left alone.
REVIEW_REFRESH = [
    "prior_year",
    "property_type",
    "prior_eui",
    "current_eui",
    "pct_change",
    "peer_z_score",
    "peer_low",
    "peer_high",
    "direction",
    "reasons",
    "severity",
    "last_flagged_at",
]


@contextmanager
def connect(database_url: str) -> Iterator[object]:
    import psycopg2

    conn = psycopg2.connect(database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        for sql_file in sorted(SQL_DIR.glob("*.sql")):
            cur.execute(sql_file.read_text())
            log.info("Applied %s", sql_file.name)


def _copy_frame(cur, staging: str, df: pd.DataFrame, columns: List[str]) -> None:
    out = df[columns].copy()
    for col in out.columns:
        # Integer columns that picked up NaNs arrive as floats ("1931.0"); COPY needs "1931".
        if col in INT_COLUMNS and pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round().astype("Int64")
    buf = io.StringIO()
    out.to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cols = ", ".join(columns)
    cur.copy_expert(f"COPY {staging} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def upsert(
    conn,
    table: str,
    df: pd.DataFrame,
    columns: List[str],
    key: List[str],
    refresh: Optional[List[str]] = None,
    extra_insert: Optional[Tuple[str, str]] = None,
) -> int:
    """COPY df into a temp staging table, then INSERT ... ON CONFLICT DO UPDATE.

    extra_insert=(target_column, select_expression) adds a column that is only set on insert.
    """
    staging = f"stg_{table}"
    cols = ", ".join(columns)
    refresh = refresh if refresh is not None else [c for c in columns if c not in key]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in refresh)
    if table != "eui_review_log":
        updates += ", updated_at = now()"
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
        cur.execute(
            f"CREATE TEMP TABLE {staging} ON COMMIT DROP AS SELECT {cols} FROM ll84.{table} WITH NO DATA"
        )
        _copy_frame(cur, staging, df, columns)
        insert_cols, select_cols = cols, cols
        if extra_insert:
            insert_cols += f", {extra_insert[0]}"
            select_cols += f", {extra_insert[1]}"
        cur.execute(
            f"INSERT INTO ll84.{table} ({insert_cols}) SELECT {select_cols} FROM {staging} "
            f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}"
        )
        affected = cur.rowcount
    log.info("Upserted %d rows into ll84.%s", affected, table)
    return affected


def load_curated(conn, tables: Dict[str, pd.DataFrame]) -> Dict[str, int]:
    counts = {}
    for name in LOAD_ORDER:
        spec = TABLES[name]
        counts[name] = upsert(conn, name, tables[name], spec["columns"], spec["key"])
    return counts


def load_flags(conn, flags: pd.DataFrame) -> int:
    """Upsert flags into the review log and auto-resolve open items that no longer trip a rule."""
    if flags.empty:
        log.info("No flags to load")
        return 0
    staged = flags.rename(columns={"flagged_at": "last_flagged_at"}).copy()
    staged["reasons"] = "{" + staged["reasons"].astype(str) + "}"
    affected = upsert(
        conn,
        "eui_review_log",
        staged,
        REVIEW_COLUMNS,
        ["property_id", "report_year", "eui_metric"],
        refresh=REVIEW_REFRESH,
        extra_insert=("first_flagged_at", "last_flagged_at"),
    )
    run_ts = staged["last_flagged_at"].max()
    years = sorted(int(y) for y in staged["report_year"].unique())
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ll84.eui_review_log
               SET status = 'resolved',
                   review_notes = concat_ws(' | ', review_notes, 'auto-resolved: no longer flagged on ' || %s::date)
             WHERE status = 'open' AND last_flagged_at < %s AND report_year = ANY(%s)
            """,
            (run_ts, run_ts, years),
        )
        if cur.rowcount:
            log.info("Auto-resolved %d stale open flags", cur.rowcount)
    return affected


def record_run(conn, step: str, status: str, details: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ll84.pipeline_runs (step, status, finished_at, details) VALUES (%s, %s, now(), %s)",
            (step, status, json.dumps(details, default=str)),
        )
