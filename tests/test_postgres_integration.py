"""Runs against a real Postgres when TEST_DATABASE_URL is set (CI provides one)."""

import os

import pytest

from ll84.flagging import FlagConfig, flag_eui_changes
from ll84.sample import make_sample
from ll84.transform import transform

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture()
def conn():
    import psycopg2

    from ll84.load_postgres import apply_schema

    c = psycopg2.connect(DB_URL)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS ll84 CASCADE")
    apply_schema(c)
    c.commit()
    yield c
    c.close()


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM ll84.{table}")
        return cur.fetchone()[0]


def test_load_is_idempotent_and_preserves_review_status(conn):
    from ll84.load_postgres import load_curated, load_flags

    tables = transform(make_sample(n_properties=300, seed=5, anomaly_rate=0.05))
    frames = {
        n: getattr(tables, n) for n in ("properties", "meters", "consumption", "annual_metrics")
    }
    flags = flag_eui_changes(tables.annual_metrics, FlagConfig())
    assert len(flags) > 0

    load_curated(conn, frames)
    load_flags(conn, flags)
    conn.commit()
    counts = {
        t: _count(conn, t)
        for t in ("properties", "meters", "consumption", "annual_metrics", "eui_review_log")
    }
    assert counts["properties"] == len(tables.properties)
    assert counts["consumption"] == len(tables.consumption)
    assert counts["eui_review_log"] == len(flags)

    # A reviewer triages one item...
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ll84.eui_review_log SET status='false_positive', reviewer='me' WHERE review_id = 1"
        )
    conn.commit()

    # ...and a re-run neither duplicates rows nor resets the review.
    load_curated(conn, frames)
    load_flags(conn, flags)
    conn.commit()
    assert {t: _count(conn, t) for t in counts} == counts
    with conn.cursor() as cur:
        cur.execute("SELECT status, reviewer FROM ll84.eui_review_log WHERE review_id = 1")
        assert cur.fetchone() == ("false_positive", "me")
        cur.execute("SELECT count(*) FROM ll84.v_open_eui_flags")
        assert cur.fetchone()[0] == len(flags) - 1
        cur.execute("SELECT count(*) FROM ll84.v_borough_year_summary")
        assert cur.fetchone()[0] > 0


def test_stale_open_flags_are_auto_resolved(conn):
    from ll84.load_postgres import load_curated, load_flags

    tables = transform(make_sample(n_properties=300, seed=5, anomaly_rate=0.05))
    load_curated(
        conn,
        {n: getattr(tables, n) for n in ("properties", "meters", "consumption", "annual_metrics")},
    )
    strict = flag_eui_changes(tables.annual_metrics, FlagConfig(pct_threshold=0.3))
    load_flags(conn, strict)
    loose = flag_eui_changes(
        tables.annual_metrics, FlagConfig(pct_threshold=0.9, min_peer_group=10_000)
    )
    load_flags(conn, loose)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ll84.eui_review_log WHERE status = 'open'")
        assert cur.fetchone()[0] == len(loose)
