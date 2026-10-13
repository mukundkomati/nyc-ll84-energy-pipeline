-- NYC LL84 energy benchmarking model (Postgres 13+). Idempotent: safe to re-run.
CREATE SCHEMA IF NOT EXISTS ll84;

-- One row per EPA Portfolio Manager property (attributes from its latest report).
CREATE TABLE IF NOT EXISTS ll84.properties (
    property_id                BIGINT PRIMARY KEY,
    property_name              TEXT,
    parent_property_id         BIGINT,
    bbl                        CHAR(10),
    bbl_count                  SMALLINT,
    bin                        CHAR(7),
    address                    TEXT,
    city                       TEXT,
    postal_code                CHAR(5),
    borough                    TEXT CHECK (borough IN ('MANHATTAN','BRONX','BROOKLYN','QUEENS','STATEN ISLAND')),
    primary_property_type      TEXT,
    largest_property_use_type  TEXT,
    year_built                 SMALLINT,
    latitude                   DOUBLE PRECISION,
    longitude                  DOUBLE PRECISION,
    first_report_year          SMALLINT,
    last_report_year           SMALLINT,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per property x energy source ("meter" channel: electricity_grid, natural_gas, district_steam, ...).
CREATE TABLE IF NOT EXISTS ll84.meters (
    meter_id           TEXT PRIMARY KEY,
    property_id        BIGINT NOT NULL REFERENCES ll84.properties (property_id) ON DELETE CASCADE,
    energy_source      TEXT NOT NULL,
    native_unit        TEXT NOT NULL,
    first_report_year  SMALLINT,
    last_report_year   SMALLINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (property_id, energy_source)
);

-- Annual consumption per meter.
CREATE TABLE IF NOT EXISTS ll84.consumption (
    meter_id      TEXT NOT NULL REFERENCES ll84.meters (meter_id) ON DELETE CASCADE,
    report_year   SMALLINT NOT NULL,
    usage_kbtu    DOUBLE PRECISION CHECK (usage_kbtu >= 0),
    usage_native  DOUBLE PRECISION,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_id, report_year)
);

-- Property-year KPIs reported under LL84.
CREATE TABLE IF NOT EXISTS ll84.annual_metrics (
    property_id               BIGINT NOT NULL REFERENCES ll84.properties (property_id) ON DELETE CASCADE,
    report_year               SMALLINT NOT NULL,
    primary_property_type     TEXT,
    gross_floor_area_ft2      DOUBLE PRECISION,
    site_eui_kbtu_ft2         DOUBLE PRECISION,
    wn_site_eui_kbtu_ft2      DOUBLE PRECISION,
    source_eui_kbtu_ft2       DOUBLE PRECISION,
    wn_source_eui_kbtu_ft2    DOUBLE PRECISION,
    site_energy_kbtu          DOUBLE PRECISION,
    source_energy_kbtu        DOUBLE PRECISION,
    energy_star_score         SMALLINT CHECK (energy_star_score BETWEEN 1 AND 100),
    ghg_emissions_tco2e       DOUBLE PRECISION,
    ghg_intensity_kgco2e_ft2  DOUBLE PRECISION,
    water_use_kgal            DOUBLE PRECISION,
    occupancy_pct             SMALLINT,
    number_of_buildings       INTEGER,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (property_id, report_year)
);

-- Review queue: one row per flagged property-year. Re-running the flagger refreshes the
-- measurements but never overwrites a reviewer's status or notes.
CREATE TABLE IF NOT EXISTS ll84.eui_review_log (
    review_id         BIGSERIAL PRIMARY KEY,
    property_id       BIGINT NOT NULL REFERENCES ll84.properties (property_id) ON DELETE CASCADE,
    report_year       SMALLINT NOT NULL,
    prior_year        SMALLINT NOT NULL,
    eui_metric        TEXT NOT NULL,
    property_type     TEXT,
    prior_eui         DOUBLE PRECISION,
    current_eui       DOUBLE PRECISION,
    pct_change        DOUBLE PRECISION,
    peer_z_score      DOUBLE PRECISION,
    peer_low          DOUBLE PRECISION,
    peer_high         DOUBLE PRECISION,
    direction         TEXT CHECK (direction IN ('increase','decrease')),
    reasons           TEXT[] NOT NULL,
    severity          TEXT NOT NULL CHECK (severity IN ('medium','high')),
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open','confirmed_issue','false_positive','resolved')),
    reviewer          TEXT,
    review_notes      TEXT,
    first_flagged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_flagged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (property_id, report_year, eui_metric)
);

-- Audit trail of pipeline runs.
CREATE TABLE IF NOT EXISTS ll84.pipeline_runs (
    run_id       BIGSERIAL PRIMARY KEY,
    step         TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running',
    details      JSONB
);

CREATE INDEX IF NOT EXISTS ix_properties_borough_type ON ll84.properties (borough, primary_property_type);
CREATE INDEX IF NOT EXISTS ix_properties_bbl ON ll84.properties (bbl);
CREATE INDEX IF NOT EXISTS ix_meters_property ON ll84.meters (property_id);
CREATE INDEX IF NOT EXISTS ix_consumption_year ON ll84.consumption (report_year);
CREATE INDEX IF NOT EXISTS ix_metrics_year_type ON ll84.annual_metrics (report_year, primary_property_type);
CREATE INDEX IF NOT EXISTS ix_review_open ON ll84.eui_review_log (status, severity) WHERE status = 'open';
