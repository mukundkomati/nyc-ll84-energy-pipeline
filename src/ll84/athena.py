"""Run Athena DDL and analytical queries over the curated Parquet."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from string import Template
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql" / "athena"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def render(sql: str, database: str, curated_uri: str) -> str:
    return Template(sql).substitute(DATABASE=database, CURATED_URI=curated_uri.rstrip("/"))


def split_statements(sql: str) -> List[str]:
    """Athena runs one statement per call. Strip `--` comment lines, split on ';'."""
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def list_queries() -> List[str]:
    return sorted(p.stem for p in (SQL_DIR / "queries").glob("*.sql"))


class AthenaClient:
    def __init__(self, workgroup: str, database: str, region: Optional[str] = None, client=None):
        import boto3

        self.workgroup = workgroup
        self.database = database
        self.client = client or boto3.client("athena", region_name=region)

    def execute(self, sql: str, timeout_s: int = 600) -> Dict:
        start = self.client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self.database},
            WorkGroup=self.workgroup,  # output location + bytes-scanned cap live on the workgroup
        )
        qid = start["QueryExecutionId"]
        deadline = time.time() + timeout_s
        delay = 0.5
        while True:
            execution = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = execution["Status"]["State"]
            if state in TERMINAL:
                break
            if time.time() > deadline:
                self.client.stop_query_execution(QueryExecutionId=qid)
                raise TimeoutError(f"Athena query {qid} timed out")
            time.sleep(delay)
            delay = min(delay * 1.5, 5)
        if state != "SUCCEEDED":
            reason = execution["Status"].get("StateChangeReason", "unknown")
            raise RuntimeError(f"Athena query {qid} {state}: {reason}")
        execution.setdefault("QueryExecutionId", qid)
        stats = execution.get("Statistics", {})
        scanned_mb = stats.get("DataScannedInBytes", 0) / 1_048_576
        log.info(
            "Query %s succeeded: %.2f MB scanned, %d ms",
            qid,
            scanned_mb,
            stats.get("EngineExecutionTimeInMillis", 0),
        )
        return execution

    def fetch(self, query_execution_id: str) -> pd.DataFrame:
        paginator = self.client.get_paginator("get_query_results")
        rows: List[List[Optional[str]]] = []
        columns: List[str] = []
        for page in paginator.paginate(QueryExecutionId=query_execution_id):
            if not columns:
                columns = [c["Name"] for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
            for row in page["ResultSet"]["Rows"]:
                rows.append([d.get("VarCharValue") for d in row["Data"]])
        if rows and rows[0] == columns:  # first row of the first page is the header
            rows = rows[1:]
        df = pd.DataFrame(rows, columns=columns)
        for col in df.columns:
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() == df[col].notna().sum():
                df[col] = converted
        return df

    def query(self, sql: str) -> pd.DataFrame:
        execution = self.execute(sql)
        return self.fetch(execution["QueryExecutionId"])


def create_tables(athena: AthenaClient, curated_uri: str) -> int:
    count = 0
    for ddl_file in sorted((SQL_DIR / "ddl").glob("*.sql")):
        for statement in split_statements(
            render(ddl_file.read_text(), athena.database, curated_uri)
        ):
            first_line = re.sub(r"\s+", " ", statement)[:80]
            log.info("Running DDL: %s...", first_line)
            athena.execute(statement)
            count += 1
    return count


def run_named_query(athena: AthenaClient, name: str, curated_uri: str) -> pd.DataFrame:
    path = SQL_DIR / "queries" / f"{name}.sql"
    if not path.exists():
        raise FileNotFoundError(f"Unknown query '{name}'. Available: {', '.join(list_queries())}")
    (statement,) = split_statements(render(path.read_text(), athena.database, curated_uri))
    return athena.query(statement)
