"""Download the LL84 dataset from NYC Open Data (Socrata SODA API) and land it in S3.

The API returns machine field names (report_year, property_id, site_eui_kbtu_ft, ...),
which are stable across releases, unlike the human column titles in the portal CSV export.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import requests

from ll84 import storage

log = logging.getLogger(__name__)

SODA_CSV_URL = "https://{domain}/resource/{dataset_id}.csv"


def _get_with_retry(
    session: requests.Session, url: str, params: dict, headers: dict, attempts: int = 5
) -> str:
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=120)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            if attempt == attempts:
                raise
            wait = 2**attempt
            log.warning(
                "Request failed (%s), retrying in %ss (%d/%d)", exc, wait, attempt, attempts
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def download_dataset(
    out_path: Path,
    dataset_id: str = "5zyy-y8am",
    domain: str = "data.cityofnewyork.us",
    years: Optional[Iterable[int]] = None,
    page_size: int = 50_000,
    app_token: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> int:
    """Page through the SODA API and write one CSV. Returns the number of data rows."""
    session = session or requests.Session()
    url = SODA_CSV_URL.format(domain=domain, dataset_id=dataset_id)
    headers = {"X-App-Token": app_token} if app_token else {}
    base_params = {"$order": ":id", "$limit": page_size}
    if years:
        year_list = ",".join(str(int(y)) for y in years)
        base_params["$where"] = f"report_year in({year_list})"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    offset = 0
    header: Optional[list] = None
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        while True:
            params = {**base_params, "$offset": offset}
            text = _get_with_retry(session, url, params, headers)
            rows = list(csv.reader(io.StringIO(text)))
            if not rows:
                break
            page_header, page_rows = rows[0], rows[1:]
            if header is None:
                header = page_header
                writer.writerow(header)
            elif page_header != header:
                # SODA omits nothing in CSV mode, but guard against column drift between pages.
                index = {name: i for i, name in enumerate(page_header)}
                page_rows = [[r[index[c]] if c in index else "" for c in header] for r in page_rows]
            writer.writerows(page_rows)
            total += len(page_rows)
            log.info("Downloaded %d rows (total %d)", len(page_rows), total)
            if len(page_rows) < page_size:
                break
            offset += page_size
    log.info("Saved %d rows to %s", total, out_path)
    return total


def land_raw(local_csv: Path, raw_uri: str, ingest_date: Optional[date] = None) -> str:
    """Copy the raw CSV into the raw zone, partitioned by ingest date (immutable history)."""
    ingest_date = ingest_date or date.today()
    target = storage.join(raw_uri, f"ingest_date={ingest_date.isoformat()}", "ll84_raw.csv")
    storage.upload_file(local_csv, target)
    log.info("Landed raw file at %s", target)
    return target


def latest_raw_file(raw_uri: str) -> str:
    """Return the most recent raw CSV (ingest_date partitions sort lexically)."""
    if raw_uri.endswith(".csv"):
        return raw_uri
    files = storage.list_uris(raw_uri, ".csv")
    if not files:
        raise FileNotFoundError(f"No raw CSV files under {raw_uri}. Run `ll84 extract` first.")
    return files[-1]
