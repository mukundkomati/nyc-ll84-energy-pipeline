import numpy as np
import pandas as pd
import pytest

from ll84.transform import (
    KWH_TO_KBTU,
    clean_raw,
    parse_bbl,
    parse_bin,
    parse_zip,
    transform,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1009960021", "1009960021"),
        ("1-00996-0021", "1009960021"),
        ("3035590001;3035740001", "3035590001"),
        ("Not Available", None),
        ("0000000000", None),
        ("9012340001", None),
        (None, None),
    ],
)
def test_parse_bbl(value, expected):
    assert parse_bbl(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1022615", "1022615"),
        ("2999999", None),
        ("1000000", None),
        ("0", None),
        ("123", None),
        (None, None),
    ],
)
def test_parse_bin(value, expected):
    assert parse_bin(value) == expected


def test_parse_zip():
    assert parse_zip("10036-1234") == "10036"
    assert parse_zip("v5v3e3") is None


def _raw(**overrides):
    row = {
        "report_year": "2023",
        "property_id": "19466816",
        "property_name": "123 West 43 Street",
        "nyc_borough_block_and_lot": "1009960021",
        "nyc_building_identification": "1022615",
        "borough": "MANHATTAN",
        "primary_property_type": "Performing Arts",
        "construction_status": "Existing",
        "site_eui_kbtu_ft": "77.6",
        "weather_normalized_site_eui": "79.9",
        "site_energy_use_kbtu": "5230067.1",
        "electricity_use_grid_purchase": "1576047.2",
        "electricity_use_grid_purchase_1": "461913",
        "natural_gas_use_kbtu": "630500",
        "district_steam_use_kbtu": "3023519.9",
        "fuel_oil_2_use_kbtu": "Not Available",
        "property_gfa_self_reported": "67424",
        "energy_star_score": "Not Available",
        "year_built": "1919",
        "report_submission_date": "2024-03-11T16:44:00.000",
    }
    row.update(overrides)
    return row


def test_not_available_becomes_null_and_numbers_parse():
    clean, _ = clean_raw(pd.DataFrame([_raw()]))
    row = clean.iloc[0]
    assert row["site_eui_kbtu_ft"] == pytest.approx(77.6)
    assert np.isnan(row["fuel_oil_2_use_kbtu"])
    assert np.isnan(row["energy_star_score"])
    assert row["bbl"] == "1009960021"


def test_keeps_latest_submission_and_drops_test_properties():
    raw = pd.DataFrame(
        [
            _raw(site_eui_kbtu_ft="10", report_submission_date="2024-01-01T00:00:00.000"),
            _raw(site_eui_kbtu_ft="20", report_submission_date="2024-02-01T00:00:00.000"),
            _raw(property_id="42", construction_status="Test"),
        ]
    )
    clean, quality = clean_raw(raw)
    assert len(clean) == 1
    assert clean.iloc[0]["site_eui_kbtu_ft"] == 20
    assert quality["rows_duplicate_property_year"] == 1
    assert quality["rows_test_or_design"] == 1


def test_borough_falls_back_to_bbl_and_bad_year_built_is_dropped():
    clean, _ = clean_raw(
        pd.DataFrame(
            [
                _raw(
                    borough="Not Available",
                    nyc_borough_block_and_lot="3012340001",
                    year_built="2088",
                )
            ]
        )
    )
    assert clean.iloc[0]["borough"] == "BROOKLYN"
    assert np.isnan(clean.iloc[0]["year_built"])


def test_negative_usage_is_nulled():
    clean, quality = clean_raw(pd.DataFrame([_raw(natural_gas_use_kbtu="-5")]))
    assert np.isnan(clean.iloc[0]["natural_gas_use_kbtu"])
    assert quality["negative_values_nulled"] == 1


def test_meters_and_consumption_from_fuel_columns():
    tables = transform(
        pd.DataFrame(
            [_raw(), _raw(report_year="2022", report_submission_date="2023-03-01T00:00:00.000")]
        )
    )
    meters = tables.meters.set_index("energy_source")
    assert set(meters.index) == {"electricity_grid", "natural_gas", "district_steam"}
    assert meters.loc["electricity_grid", "meter_id"] == "19466816-electricity_grid"
    assert meters.loc["natural_gas", "native_unit"] == "therms"
    assert (meters["first_report_year"] == 2022).all()

    cons = tables.consumption
    assert len(cons) == 6  # 3 sources x 2 years
    gas = cons[(cons.energy_source == "natural_gas") & (cons.report_year == 2023)].iloc[0]
    assert gas.usage_native == pytest.approx(6305.0)  # kBtu / 100 = therms
    elec = cons[(cons.energy_source == "electricity_grid") & (cons.report_year == 2023)].iloc[0]
    assert elec.usage_native == pytest.approx(461913)
    assert elec.usage_kbtu == pytest.approx(461913 * KWH_TO_KBTU, rel=1e-4)

    # Components add up to the reported site energy.
    total_2023 = cons[cons.report_year == 2023].usage_kbtu.sum()
    assert total_2023 == pytest.approx(5230067.1, rel=1e-6)


def test_properties_take_latest_attributes():
    raw = pd.DataFrame(
        [
            _raw(
                report_year="2022",
                property_name="Old Name",
                report_submission_date="2023-03-01T00:00:00.000",
            ),
            _raw(report_year="2023", property_name="New Name"),
        ]
    )
    props = transform(raw).properties
    assert len(props) == 1
    assert props.iloc[0]["property_name"] == "New Name"
    assert props.iloc[0]["first_report_year"] == 2022
    assert props.iloc[0]["last_report_year"] == 2023


def test_sample_end_to_end_shapes(sample_raw):
    tables = transform(sample_raw)
    am = tables.annual_metrics
    assert not am.duplicated(["property_id", "report_year"]).any()
    assert tables.properties["property_id"].is_unique
    assert tables.meters["meter_id"].is_unique
    assert set(tables.consumption["meter_id"]) <= set(tables.meters["meter_id"])
    assert 1 not in set(tables.properties["property_id"])  # the Test property
    assert set(am["report_year"]) == {2022, 2023, 2024}
    assert am["energy_star_score"].dropna().between(1, 100).all()
