-- External tables over the curated Parquet written by the Glue job.
-- Year-partitioned tables use partition projection, so new years are queryable
-- immediately with no crawler or MSCK REPAIR, and WHERE report_year = ... prunes S3 reads.

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.properties (
    property_id bigint,
    property_name string,
    parent_property_id bigint,
    bbl string,
    bbl_count bigint,
    bin string,
    address string,
    city string,
    postal_code string,
    borough string,
    primary_property_type string,
    largest_property_use_type string,
    year_built bigint,
    latitude double,
    longitude double,
    first_report_year bigint,
    last_report_year bigint
)
STORED AS PARQUET
LOCATION '${CURATED_URI}/properties/';

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.meters (
    meter_id string,
    property_id bigint,
    energy_source string,
    native_unit string,
    first_report_year bigint,
    last_report_year bigint
)
STORED AS PARQUET
LOCATION '${CURATED_URI}/meters/';

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.consumption (
    meter_id string,
    property_id bigint,
    energy_source string,
    usage_kbtu double,
    usage_native double
)
PARTITIONED BY (report_year int)
STORED AS PARQUET
LOCATION '${CURATED_URI}/consumption/'
TBLPROPERTIES (
    'projection.enabled' = 'true',
    'projection.report_year.type' = 'integer',
    'projection.report_year.range' = '2010,2040',
    'storage.location.template' = '${CURATED_URI}/consumption/report_year=$${report_year}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.annual_metrics (
    property_id bigint,
    primary_property_type string,
    gross_floor_area_ft2 double,
    site_eui_kbtu_ft2 double,
    wn_site_eui_kbtu_ft2 double,
    source_eui_kbtu_ft2 double,
    wn_source_eui_kbtu_ft2 double,
    site_energy_kbtu double,
    source_energy_kbtu double,
    energy_star_score bigint,
    ghg_emissions_tco2e double,
    ghg_intensity_kgco2e_ft2 double,
    water_use_kgal double,
    occupancy_pct bigint,
    number_of_buildings bigint
)
PARTITIONED BY (report_year int)
STORED AS PARQUET
LOCATION '${CURATED_URI}/annual_metrics/'
TBLPROPERTIES (
    'projection.enabled' = 'true',
    'projection.report_year.type' = 'integer',
    'projection.report_year.range' = '2010,2040',
    'storage.location.template' = '${CURATED_URI}/annual_metrics/report_year=$${report_year}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.eui_flags (
    property_id bigint,
    prior_year bigint,
    eui_metric string,
    property_type string,
    prior_eui double,
    current_eui double,
    pct_change double,
    peer_z_score double,
    peer_low double,
    peer_high double,
    direction string,
    reasons string,
    severity string,
    flagged_at timestamp
)
PARTITIONED BY (report_year int)
STORED AS PARQUET
LOCATION '${CURATED_URI}/eui_flags/'
TBLPROPERTIES (
    'projection.enabled' = 'true',
    'projection.report_year.type' = 'integer',
    'projection.report_year.range' = '2010,2040',
    'storage.location.template' = '${CURATED_URI}/eui_flags/report_year=$${report_year}/'
);
