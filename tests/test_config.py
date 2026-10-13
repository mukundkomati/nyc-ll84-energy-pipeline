from ll84.config import get_settings


def test_env_example_parses_with_inline_comments(tmp_path, monkeypatch):
    for key in (
        "LL84_DATASET_ID",
        "SOCRATA_APP_TOKEN",
        "S3_BUCKET",
        "YOY_PCT_THRESHOLD",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    s = get_settings(".env.example")
    assert s.dataset_id == "5zyy-y8am"
    assert s.socrata_app_token is None
    assert s.s3_bucket is None
    assert s.yoy_pct_threshold == 0.5
    assert s.curated_uri == "data/curated"


def test_quoted_values(tmp_path, monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    env = tmp_path / ".env"
    env.write_text('S3_BUCKET="my-bucket"  # comment\n')
    assert get_settings(env).curated_uri == "s3://my-bucket/curated/ll84"
