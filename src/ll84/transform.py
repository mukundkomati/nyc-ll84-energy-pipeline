"""Clean the raw LL84 extract and model it into curated tables.

Raw shape: one wide row per (property_id, report_year) with ~250 text columns, where
missing values are spelled "Not Available" and numbers arrive as strings.

Curated tables:
  properties      one row per EPA property_id (attributes from its latest report)
  meters          one row per property x energy source that ever reported usage
  consumption     annual usage per meter per year (kBtu + native unit)
  annual_metrics  property-year KPIs: EUI, GHG, ENERGY STAR score, water, floor area

LL84 publishes annual totals by fuel type, not individual utility meter IDs, so a
"meter" here is a property's energy-source channel (e.g. 19466816-natural_gas).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

NA_TOKENS = {
    "",
    "not available",
    "not applicable: standalone property",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
}

BOROUGHS = {"MANHATTAN", "BRONX", "BROOKLYN", "QUEENS", "STATEN ISLAND"}
BOROUGH_BY_BBL_DIGIT = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND",
}

# energy_source -> (raw kBtu column, raw native column or None, native unit, kBtu per native unit)
ENERGY_SOURCES: Dict[str, Tuple[str, Optional[str], str, Optional[float]]] = {
    "electricity_grid": (
        "electricity_use_grid_purchase",
        "electricity_use_grid_purchase_1",
        "kWh",
        None,
    ),
    "natural_gas": ("natural_gas_use_kbtu", None, "therms", 100.0),
    "district_steam": ("district_steam_use_kbtu", None, "kBtu", 1.0),
    "district_hot_water": ("district_hot_water_use_kbtu", None, "kBtu", 1.0),
    "district_chilled_water": ("district_chilled_water_use", None, "kBtu", 1.0),
    "fuel_oil_1": ("fuel_oil_1_use_kbtu", None, "kBtu", 1.0),
    "fuel_oil_2": ("fuel_oil_2_use_kbtu", None, "kBtu", 1.0),
    "fuel_oil_4": ("fuel_oil_4_use_kbtu", None, "kBtu", 1.0),
    "fuel_oil_5_6": ("fuel_oil_5_6_use_kbtu", None, "kBtu", 1.0),
    "diesel_2": ("diesel_2_use_kbtu", None, "kBtu", 1.0),
    "propane": ("propane_use_kbtu", None, "kBtu", 1.0),
    "kerosene": ("kerosene_use_kbtu", None, "kBtu", 1.0),
}
KWH_TO_KBTU = 3.412

# raw column -> curated column
METRIC_COLUMNS = {
    "property_gfa_self_reported": "gross_floor_area_ft2",
    "site_eui_kbtu_ft": "site_eui_kbtu_ft2",
    "weather_normalized_site_eui": "wn_site_eui_kbtu_ft2",
    "source_eui_kbtu_ft": "source_eui_kbtu_ft2",
    "weather_normalized_source": "wn_source_eui_kbtu_ft2",
    "site_energy_use_kbtu": "site_energy_kbtu",
    "source_energy_use_kbtu": "source_energy_kbtu",
    "energy_star_score": "energy_star_score",
    "total_location_based_ghg": "ghg_emissions_tco2e",
    "total_location_based_ghg_1": "ghg_intensity_kgco2e_ft2",
    "water_use_all_water_sources": "water_use_kgal",
    "occupancy": "occupancy_pct",
    "number_of_buildings": "number_of_buildings",
}
INT_METRICS = {"energy_star_score", "occupancy_pct", "number_of_buildings"}

TEXT_COLUMNS = [
    "property_name",
    "nyc_borough_block_and_lot",
    "nyc_building_identification",
    "address_1",
    "city",
    "postal_code",
    "borough",
    "primary_property_type",
    "largest_property_use_type",
    "construction_status",
    "report_submission_date",
]
NUMERIC_COLUMNS = [
    "report_year",
    "property_id",
    "parent_property_id",
    "year_built",
    "latitude",
    "longitude",
    "property_gfa_calculated",
    *METRIC_COLUMNS.keys(),
    *[src[0] for src in ENERGY_SOURCES.values()],
    *[src[1] for src in ENERGY_SOURCES.values() if src[1]],
]

PROPERTY_COLUMNS = [
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
]
METER_COLUMNS = [
    "meter_id",
    "property_id",
    "energy_source",
    "native_unit",
    "first_report_year",
    "last_report_year",
]
CONSUMPTION_COLUMNS = [
    "meter_id",
    "property_id",
    "energy_source",
    "report_year",
    "usage_kbtu",
    "usage_native",
]
ANNUAL_METRIC_COLUMNS = [
    "property_id",
    "report_year",
    "primary_property_type",
    *METRIC_COLUMNS.values(),
]


@dataclass
class CuratedTables:
    properties: pd.DataFrame
    meters: pd.DataFrame
    consumption: pd.DataFrame
    annual_metrics: pd.DataFrame
    quality: Dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def _text(series: pd.Series) -> pd.Series:
    s = series.astype(object).where(series.notna(), None)
    s = s.map(lambda v: v.strip() if isinstance(v, str) else v)
    return s.map(lambda v: None if v is None or str(v).strip().lower() in NA_TOKENS else v)


def _num(series: pd.Series) -> pd.Series:
    s = _text(series).map(lambda v: v.replace(",", "") if isinstance(v, str) else v)
    return pd.to_numeric(s, errors="coerce").astype("float64")


def _first_token(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    tokens = [t for t in re.split(r"[;,\s]+", str(value).strip()) if t]
    return tokens[0] if tokens else None


def parse_bbl(value: Optional[str]) -> Optional[str]:
    """First valid 10-digit BBL (borough 1-5) from a possibly multi-valued field."""
    if value is None:
        return None
    for token in re.split(r"[;,\s]+", str(value).strip()):
        digits = re.sub(r"\D", "", token)
        if len(digits) == 10 and digits[0] in BOROUGH_BY_BBL_DIGIT and digits[1:6] != "00000":
            return digits
    return None


def count_bbls(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    return sum(
        1 for t in re.split(r"[;,\s]+", str(value).strip()) if len(re.sub(r"\D", "", t)) == 10
    )


def parse_bin(value: Optional[str]) -> Optional[str]:
    """7-digit BIN; drops DOB placeholder BINs such as 1000000 / 2999999."""
    token = _first_token(value)
    if token is None:
        return None
    digits = re.sub(r"\D", "", token)
    if (
        len(digits) != 7
        or digits[0] not in BOROUGH_BY_BBL_DIGIT
        or digits[1:] in {"000000", "999999"}
    ):
        return None
    return digits


def parse_zip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    match = re.match(r"^\s*(\d{5})", str(value))
    return match.group(1) if match else None


def _to_int64(series: pd.Series) -> pd.Series:
    return series.round().astype("Int64")


# ---------------------------------------------------------------- cleaning


def clean_raw(raw: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Normalize types, sentinels and identifiers; one row per (property_id, report_year)."""
    quality: Dict[str, object] = {"rows_in": int(len(raw))}
    df = raw.reindex(columns=sorted(set(raw.columns) | set(TEXT_COLUMNS) | set(NUMERIC_COLUMNS)))

    for col in TEXT_COLUMNS:
        df[col] = _text(df[col])
    for col in NUMERIC_COLUMNS:
        df[col] = _num(df[col])

    missing_keys = df["property_id"].isna() | df["report_year"].isna()
    quality["rows_missing_keys"] = int(missing_keys.sum())
    df = df[~missing_keys].copy()

    non_existing = df["construction_status"].fillna("Existing").str.lower().isin({"test", "design"})
    quality["rows_test_or_design"] = int(non_existing.sum())
    df = df[~non_existing].copy()

    df["property_id"] = _to_int64(df["property_id"])
    df["report_year"] = _to_int64(df["report_year"])

    # Resubmissions: keep the latest submission per property-year.
    df["_submitted"] = pd.to_datetime(df["report_submission_date"], errors="coerce")
    before = len(df)
    df = df.sort_values(["property_id", "report_year", "_submitted"], na_position="first")
    df = df.drop_duplicates(["property_id", "report_year"], keep="last")
    quality["rows_duplicate_property_year"] = int(before - len(df))

    # Negative energy/area values are reporting errors; treat as missing.
    numeric_measures = [
        c
        for c in NUMERIC_COLUMNS
        if c
        not in {
            "report_year",
            "property_id",
            "latitude",
            "longitude",
            "parent_property_id",
            "year_built",
        }
    ]
    negatives = (df[numeric_measures] < 0).sum().sum()
    quality["negative_values_nulled"] = int(negatives)
    df[numeric_measures] = df[numeric_measures].mask(df[numeric_measures] < 0)

    # Identifiers
    df["bbl"] = df["nyc_borough_block_and_lot"].map(parse_bbl)
    df["bbl_count"] = df["nyc_borough_block_and_lot"].map(count_bbls)
    df["bin"] = df["nyc_building_identification"].map(parse_bin)
    df["postal_code"] = df["postal_code"].map(parse_zip)
    borough = df["borough"].map(lambda v: v.upper() if isinstance(v, str) else None)
    borough = borough.where(borough.isin(BOROUGHS))
    from_bbl = df["bbl"].map(
        lambda b: BOROUGH_BY_BBL_DIGIT.get(b[0]) if isinstance(b, str) else None
    )
    df["borough"] = borough.fillna(from_bbl)
    df["address"] = df["address_1"]

    # Plausibility: year built between 1600 and the report year; NYC lat/lon box.
    yb = df["year_built"]
    df["year_built"] = yb.where((yb >= 1600) & (yb <= df["report_year"].astype("float64")))
    in_nyc = df["latitude"].between(40.4, 41.0) & df["longitude"].between(-74.3, -73.6)
    df.loc[~in_nyc, ["latitude", "longitude"]] = np.nan

    # Floor area: self-reported, falling back to Portfolio Manager's calculated GFA.
    df["property_gfa_self_reported"] = df["property_gfa_self_reported"].fillna(
        df["property_gfa_calculated"]
    )
    df.loc[df["property_gfa_self_reported"] <= 0, "property_gfa_self_reported"] = np.nan

    quality["rows_out"] = int(len(df))
    quality["rows_by_year"] = {
        int(k): int(v) for k, v in df["report_year"].value_counts().sort_index().items()
    }
    quality["bbl_valid_pct"] = round(float(df["bbl"].notna().mean() * 100), 1) if len(df) else 0.0
    quality["site_eui_present_pct"] = (
        round(float(df["site_eui_kbtu_ft"].notna().mean() * 100), 1) if len(df) else 0.0
    )
    return df.drop(columns=["_submitted"]), quality


# ---------------------------------------------------------------- modeling


def build_properties(clean: pd.DataFrame) -> pd.DataFrame:
    years = clean.groupby("property_id")["report_year"].agg(
        first_report_year="min", last_report_year="max"
    )
    latest = clean.sort_values(["property_id", "report_year"]).drop_duplicates(
        "property_id", keep="last"
    )
    props = latest.set_index("property_id").join(years).reset_index()
    props["parent_property_id"] = _to_int64(props["parent_property_id"])
    props["year_built"] = _to_int64(props["year_built"])
    props["bbl_count"] = props["bbl_count"].astype("Int64")
    props["first_report_year"] = props["first_report_year"].astype("Int64")
    props["last_report_year"] = props["last_report_year"].astype("Int64")
    return props[PROPERTY_COLUMNS].sort_values("property_id").reset_index(drop=True)


def build_consumption(clean: pd.DataFrame) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for source, (kbtu_col, native_col, _unit, kbtu_per_native) in ENERGY_SOURCES.items():
        kbtu = clean[kbtu_col]
        if native_col is not None:
            native = clean[native_col].fillna(kbtu / KWH_TO_KBTU)
        else:
            native = kbtu / kbtu_per_native
        part = pd.DataFrame(
            {
                "property_id": clean["property_id"].values,
                "report_year": clean["report_year"].values,
                "energy_source": source,
                "usage_kbtu": kbtu.values,
                "usage_native": native.values,
            }
        )
        frames.append(part[part["usage_kbtu"].notna()])
    cons = pd.concat(frames, ignore_index=True)
    cons["property_id"] = cons["property_id"].astype("int64")
    cons["report_year"] = cons["report_year"].astype("int64")
    cons["meter_id"] = cons["property_id"].astype(str) + "-" + cons["energy_source"]
    return (
        cons[CONSUMPTION_COLUMNS]
        .sort_values(["property_id", "energy_source", "report_year"])
        .reset_index(drop=True)
    )


def build_meters(consumption: pd.DataFrame) -> pd.DataFrame:
    meters = consumption.groupby(["meter_id", "property_id", "energy_source"], as_index=False)[
        "report_year"
    ].agg(first_report_year="min", last_report_year="max")
    meters["native_unit"] = meters["energy_source"].map(
        {k: v[2] for k, v in ENERGY_SOURCES.items()}
    )
    return meters[METER_COLUMNS].sort_values("meter_id").reset_index(drop=True)


def build_annual_metrics(clean: pd.DataFrame) -> pd.DataFrame:
    metrics = clean[
        ["property_id", "report_year", "primary_property_type", *METRIC_COLUMNS.keys()]
    ].rename(columns=METRIC_COLUMNS)
    for col in INT_METRICS:
        metrics[col] = _to_int64(metrics[col])
    metrics["energy_star_score"] = metrics["energy_star_score"].where(
        metrics["energy_star_score"].between(1, 100)
    )
    metrics["property_id"] = metrics["property_id"].astype("int64")
    metrics["report_year"] = metrics["report_year"].astype("int64")
    return (
        metrics[ANNUAL_METRIC_COLUMNS]
        .sort_values(["property_id", "report_year"])
        .reset_index(drop=True)
    )


def transform(raw: pd.DataFrame) -> CuratedTables:
    clean, quality = clean_raw(raw)
    consumption = build_consumption(clean)
    tables = CuratedTables(
        properties=build_properties(clean),
        meters=build_meters(consumption),
        consumption=consumption,
        annual_metrics=build_annual_metrics(clean),
        quality=quality,
    )
    quality.update(
        properties=len(tables.properties),
        meters=len(tables.meters),
        consumption_rows=len(tables.consumption),
        annual_metric_rows=len(tables.annual_metrics),
    )
    log.info("Transform quality report: %s", quality)
    return tables
