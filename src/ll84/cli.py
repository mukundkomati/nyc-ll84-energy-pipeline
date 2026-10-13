"""Command line entry point: `python -m ll84 <command>` (or `ll84 <command>` once installed)."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from ll84 import __version__
from ll84.config import Settings, get_settings
from ll84.flagging import FlagConfig
from ll84.logging_setup import configure

log = logging.getLogger("ll84")


def _flag_config(s: Settings) -> FlagConfig:
    return FlagConfig(
        pct_threshold=s.yoy_pct_threshold,
        z_threshold=s.peer_z_threshold,
        iqr_multiplier=s.iqr_multiplier,
        min_peer_group=s.min_peer_group,
    )


def _require_db(s: Settings) -> str:
    if not s.database_url:
        sys.exit("DATABASE_URL is not set (see .env.example)")
    return s.database_url


def cmd_extract(args, s: Settings) -> None:
    from ll84.extract import download_dataset, land_raw

    local_csv = s.local_data_dir / "downloads" / f"ll84_{date.today().isoformat()}.csv"
    download_dataset(
        local_csv, s.dataset_id, s.socrata_domain, args.years, app_token=s.socrata_app_token
    )
    land_raw(local_csv, s.raw_uri)


def cmd_sample(args, s: Settings) -> None:
    from ll84.extract import land_raw
    from ll84.sample import make_sample

    local_csv = s.local_data_dir / "downloads" / "ll84_sample.csv"
    local_csv.parent.mkdir(parents=True, exist_ok=True)
    make_sample(n_properties=args.properties, seed=args.seed).to_csv(local_csv, index=False)
    land_raw(local_csv, s.raw_uri)


def cmd_transform(args, s: Settings) -> None:
    from ll84.pipeline import run_transform

    report = run_transform(
        args.raw or s.raw_uri,
        args.curated or s.curated_uri,
        None if args.no_flags else _flag_config(s),
    )
    log.info("Transform complete: %s", report)


def cmd_flag(args, s: Settings) -> None:
    from ll84.pipeline import run_flags

    run_flags(args.curated or s.curated_uri, _flag_config(s))


def cmd_glue_run(args, s: Settings) -> None:
    from ll84.glue_runner import run_glue_job

    run_glue_job(s.glue_job_name, region=s.aws_region)


def _athena(s: Settings):
    from ll84.athena import AthenaClient

    return AthenaClient(s.athena_workgroup, s.glue_database, s.aws_region)


def cmd_athena_setup(args, s: Settings) -> None:
    from ll84.athena import create_tables

    if not s.curated_uri.startswith("s3://"):
        sys.exit("Athena needs S3: set S3_BUCKET in .env")
    n = create_tables(_athena(s), s.curated_uri)
    log.info("Ran %d DDL statements", n)


def cmd_athena_query(args, s: Settings) -> None:
    import pandas as pd

    from ll84.athena import list_queries, run_named_query

    if args.name == "list":
        print("\n".join(list_queries()))
        return
    df = run_named_query(_athena(s), args.name, s.curated_uri)
    with pd.option_context(
        "display.max_rows", 200, "display.width", 200, "display.max_columns", 20
    ):
        print(df.to_string(index=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        log.info("Saved results to %s", args.out)


def cmd_db_init(args, s: Settings) -> None:
    from ll84.load_postgres import apply_schema, connect

    with connect(_require_db(s)) as conn:
        apply_schema(conn)


def cmd_load(args, s: Settings) -> None:
    from ll84.load_postgres import apply_schema, connect, load_curated, load_flags, record_run
    from ll84.pipeline import read_curated, read_flags

    curated = args.curated or s.curated_uri
    tables = read_curated(curated)
    flags = read_flags(curated)
    with connect(_require_db(s)) as conn:
        apply_schema(conn)
        counts = load_curated(conn, tables)
        counts["eui_review_log"] = load_flags(conn, flags)
        record_run(conn, "load_postgres", "succeeded", {"source": curated, "rows": counts})
    log.info("Postgres load complete: %s", counts)


def cmd_review(args, s: Settings) -> None:
    import pandas as pd

    from ll84.load_postgres import connect

    with connect(_require_db(s)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT review_id, property_id, property_name, borough, property_type, prior_year, report_year, "
            "prior_eui, current_eui, pct_change, reasons, severity FROM ll84.v_open_eui_flags LIMIT %s",
            (args.limit,),
        )
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    with pd.option_context(
        "display.width", 220, "display.max_columns", 20, "display.max_colwidth", 40
    ):
        print(df.to_string(index=False) if len(df) else "No open flags.")


def cmd_run_local(args, s: Settings) -> None:
    if args.sample:
        cmd_sample(argparse.Namespace(properties=args.properties, seed=42), s)
    else:
        cmd_extract(argparse.Namespace(years=args.years), s)
    cmd_transform(argparse.Namespace(raw=None, curated=None, no_flags=False), s)
    if s.database_url:
        cmd_load(argparse.Namespace(curated=None), s)
    else:
        log.info("DATABASE_URL not set; skipping Postgres load")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ll84", description="NYC LL84 building energy pipeline")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--env-file", default=".env")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser(
        "extract", help="Download LL84 from NYC Open Data and land it in the raw zone"
    )
    e.add_argument("--years", type=int, nargs="*", help="Only these report years (default: all)")
    e.set_defaults(func=cmd_extract)

    sm = sub.add_parser(
        "sample", help="Generate a synthetic LL84-shaped extract (offline demo/testing)"
    )
    sm.add_argument("--properties", type=int, default=2000)
    sm.add_argument("--seed", type=int, default=42)
    sm.set_defaults(func=cmd_sample)

    t = sub.add_parser(
        "transform", help="Raw CSV -> curated Parquet (+ EUI flags), same code the Glue job runs"
    )
    t.add_argument("--raw", help="Raw URI (file or prefix); default from settings")
    t.add_argument("--curated", help="Curated URI; default from settings")
    t.add_argument("--no-flags", action="store_true")
    t.set_defaults(func=cmd_transform)

    f = sub.add_parser("flag", help="Re-run year-over-year EUI flagging on curated data")
    f.add_argument("--curated")
    f.set_defaults(func=cmd_flag)

    sub.add_parser("glue-run", help="Start the Glue job and wait for it").set_defaults(
        func=cmd_glue_run
    )
    sub.add_parser("athena-setup", help="Create Athena database + external tables").set_defaults(
        func=cmd_athena_setup
    )

    q = sub.add_parser("athena-query", help="Run a named Athena query (use 'list' to see them)")
    q.add_argument("name")
    q.add_argument("--out", help="Save results to this CSV path")
    q.set_defaults(func=cmd_athena_query)

    sub.add_parser("db-init", help="Create the Postgres schema").set_defaults(func=cmd_db_init)

    ld = sub.add_parser("load-postgres", help="Upsert curated tables and EUI flags into Postgres")
    ld.add_argument("--curated")
    ld.set_defaults(func=cmd_load)

    r = sub.add_parser("review", help="Show open EUI review items from Postgres")
    r.add_argument("--limit", type=int, default=25)
    r.set_defaults(func=cmd_review)

    rl = sub.add_parser(
        "run-local", help="extract (or sample) -> transform -> flag -> load Postgres"
    )
    rl.add_argument(
        "--sample", action="store_true", help="Use synthetic data instead of downloading"
    )
    rl.add_argument("--properties", type=int, default=2000)
    rl.add_argument("--years", type=int, nargs="*")
    rl.set_defaults(func=cmd_run_local)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings(args.env_file)
    configure(settings.log_dir)
    args.func(args, settings)
    return 0
