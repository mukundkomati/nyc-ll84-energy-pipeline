"""Tiny storage layer so the same code runs against S3 (s3://...) or a local folder.

Tables are written as Parquet in Hive-style partitions (report_year=2023/part-0000.parquet)
so Athena can prune partitions and only scan the years a query touches.
"""

from __future__ import annotations

import io
import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)


def is_s3(uri: str) -> bool:
    return uri.startswith("s3://")


def split_s3(uri: str) -> Tuple[str, str]:
    without = uri[len("s3://") :]
    bucket, _, key = without.partition("/")
    return bucket, key


def _s3():
    import boto3

    return boto3.client("s3")


def join(base: str, *parts: str) -> str:
    base = base.rstrip("/")
    return "/".join([base, *[p.strip("/") for p in parts]])


def write_bytes(uri: str, data: bytes) -> None:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        _s3().put_object(Bucket=bucket, Key=key, Body=data)
    else:
        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def read_bytes(uri: str) -> bytes:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        return _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(uri).read_bytes()


def upload_file(local_path: str | Path, uri: str) -> None:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        _s3().upload_file(str(local_path), bucket, key)
    else:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, uri)


def list_uris(prefix_uri: str, suffix: str = "") -> List[str]:
    if is_s3(prefix_uri):
        bucket, prefix = split_s3(prefix_uri.rstrip("/") + "/")
        paginator = _s3().get_paginator("list_objects_v2")
        out = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(suffix):
                    out.append(f"s3://{bucket}/{obj['Key']}")
        return sorted(out)
    root = Path(prefix_uri)
    if root.is_file():
        return [str(root)]
    if not root.exists():
        return []
    return sorted(str(p) for p in root.rglob(f"*{suffix}") if p.is_file())


def delete_prefix(prefix_uri: str) -> None:
    if is_s3(prefix_uri):
        bucket, prefix = split_s3(prefix_uri.rstrip("/") + "/")
        client = _s3()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
    else:
        shutil.rmtree(prefix_uri, ignore_errors=True)


def _to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(
        buf,
        index=False,
        engine="pyarrow",
        coerce_timestamps="ms",
        allow_truncated_timestamps=True,
    )
    return buf.getvalue()


def write_table(
    df: pd.DataFrame, base_uri: str, table: str, partition_col: Optional[str] = None
) -> int:
    """Full-refresh write of one table. Returns number of files written."""
    table_uri = join(base_uri, table)
    delete_prefix(table_uri)
    if partition_col is None:
        write_bytes(join(table_uri, "part-0000.parquet"), _to_parquet_bytes(df))
        return 1
    files = 0
    for value, part in df.groupby(partition_col, sort=True):
        payload = part.drop(columns=[partition_col])
        write_bytes(
            join(table_uri, f"{partition_col}={int(value)}", "part-0000.parquet"),
            _to_parquet_bytes(payload),
        )
        files += 1
    log.info("Wrote %s (%d rows, %d partitions) to %s", table, len(df), files, table_uri)
    return files


def read_table(base_uri: str, table: str) -> pd.DataFrame:
    table_uri = join(base_uri, table)
    frames = []
    for uri in list_uris(table_uri, ".parquet"):
        part = pd.read_parquet(io.BytesIO(read_bytes(uri)))
        # Re-attach Hive partition columns from the path (e.g. report_year=2023).
        for segment in uri[len(table_uri) :].split("/"):
            if "=" in segment:
                key, value = segment.split("=", 1)
                part[key] = int(value) if value.isdigit() else value
        frames.append(part)
    if not frames:
        raise FileNotFoundError(f"No parquet files found under {table_uri}")
    return pd.concat(frames, ignore_index=True)
