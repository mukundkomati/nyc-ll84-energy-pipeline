import pandas as pd

from ll84 import storage
from ll84.athena import SQL_DIR, AthenaClient, list_queries, render, split_statements
from ll84.pipeline import read_curated, run_transform
from ll84.sample import make_sample


def test_partitioned_roundtrip(tmp_path):
    df = pd.DataFrame(
        {"property_id": [1, 2, 3], "report_year": [2022, 2023, 2023], "eui": [1.5, 2.5, None]}
    )
    files = storage.write_table(df, str(tmp_path), "t", "report_year")
    assert files == 2
    assert (tmp_path / "t" / "report_year=2023" / "part-0000.parquet").exists()
    back = storage.read_table(str(tmp_path), "t").sort_values("property_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(back[df.columns], df, check_dtype=False)


def test_write_table_is_full_refresh(tmp_path):
    storage.write_table(
        pd.DataFrame({"report_year": [2020], "x": [1]}), str(tmp_path), "t", "report_year"
    )
    storage.write_table(
        pd.DataFrame({"report_year": [2021], "x": [2]}), str(tmp_path), "t", "report_year"
    )
    assert list(storage.read_table(str(tmp_path), "t")["report_year"]) == [2021]


def test_run_transform_local(tmp_path):
    raw_dir = tmp_path / "raw" / "ingest_date=2026-01-01"
    raw_dir.mkdir(parents=True)
    make_sample(n_properties=150, seed=3).to_csv(raw_dir / "ll84_raw.csv", index=False)
    report = run_transform(str(tmp_path / "raw"), str(tmp_path / "curated"), None)
    assert report["properties"] > 100
    curated = read_curated(str(tmp_path / "curated"))
    assert set(curated) == {"properties", "meters", "consumption", "annual_metrics"}
    assert set(curated["annual_metrics"]["report_year"]) == {2022, 2023, 2024}


def test_ddl_renders_athena_projection_placeholder():
    sql = render(
        (SQL_DIR / "ddl" / "10_tables.sql").read_text(), "ll84", "s3://bucket/curated/ll84/"
    )
    statements = split_statements(sql)
    assert len(statements) == 5
    assert "report_year=${report_year}" in sql  # escaped $$ survives for Athena
    assert "s3://bucket/curated/ll84/annual_metrics/" in sql
    assert all(not s.startswith("--") for s in statements)


def test_every_named_query_renders_to_one_statement():
    for name in list_queries():
        sql = render((SQL_DIR / "queries" / f"{name}.sql").read_text(), "ll84", "s3://b/c")
        assert len(split_statements(sql)) == 1, name
        assert "${" not in sql


class FakeAthena:
    def __init__(self):
        self.started = []

    def start_query_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):
        return {
            "QueryExecution": {
                "Status": {"State": "SUCCEEDED"},
                "Statistics": {"DataScannedInBytes": 2048},
            }
        }

    def get_paginator(self, _name):
        class P:
            def paginate(self, QueryExecutionId):
                cols = [{"Name": "borough"}, {"Name": "buildings"}]
                yield {
                    "ResultSet": {
                        "ResultSetMetadata": {"ColumnInfo": cols},
                        "Rows": [
                            {"Data": [{"VarCharValue": "borough"}, {"VarCharValue": "buildings"}]},
                            {"Data": [{"VarCharValue": "BRONX"}, {"VarCharValue": "12"}]},
                        ],
                    }
                }

        return P()


def test_athena_client_uses_workgroup_and_parses_results():
    fake = FakeAthena()
    df = AthenaClient("ll84-wg", "ll84", client=fake).query("SELECT 1")
    assert fake.started[0]["WorkGroup"] == "ll84-wg"
    assert list(df.columns) == ["borough", "buildings"]
    assert df.iloc[0]["buildings"] == 12
