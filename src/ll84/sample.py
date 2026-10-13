"""Synthetic LL84-shaped data for offline demos and tests.

Uses the real SODA field names and quirks (numbers as text, "Not Available" sentinels,
multi-BBL fields, placeholder BINs, resubmitted duplicates, Test properties) and plants
known year-over-year EUI anomalies so the flagger has something to find.
This is NOT real data; use `ll84 extract` for the real dataset.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

NA = "Not Available"

# type -> (share, median site EUI, electricity share, gets ENERGY STAR score)
TYPES = {
    "Multifamily Housing": (0.62, 82, 0.25, True),
    "Office": (0.12, 76, 0.55, True),
    "K-12 School": (0.08, 68, 0.35, True),
    "Hotel": (0.05, 115, 0.45, True),
    "Non-Refrigerated Warehouse": (0.05, 42, 0.6, True),
    "College/University": (0.04, 120, 0.5, False),
    "Hospital (General Medical & Surgical)": (0.04, 225, 0.4, True),
}
BOROUGHS = [
    ("MANHATTAN", "1", 40.78, -73.97),
    ("BRONX", "2", 40.85, -73.88),
    ("BROOKLYN", "3", 40.66, -73.95),
    ("QUEENS", "4", 40.72, -73.82),
    ("STATEN ISLAND", "5", 40.59, -74.13),
]
TON_PER_KBTU = {"elec": 0.0000845, "gas": 0.0000531, "steam": 0.0000444, "oil": 0.0000742}


def _fmt(x: Optional[float], digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return NA
    return f"{round(float(x), digits):g}" if digits else str(int(round(x)))


def make_sample(
    n_properties: int = 2000, years=(2022, 2023, 2024), seed: int = 42, anomaly_rate: float = 0.02
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    type_names = list(TYPES)
    shares = np.array([TYPES[t][0] for t in type_names])
    rows = []
    for i in range(n_properties):
        pid = 2_000_000 + i * 7
        ptype = type_names[rng.choice(len(type_names), p=shares / shares.sum())]
        _, median_eui, elec_share, scored = TYPES[ptype]
        boro, digit, lat0, lon0 = BOROUGHS[rng.integers(0, 5)]
        gfa = float(np.round(rng.lognormal(np.log(90_000), 0.6), -2))
        base_eui = median_eui * rng.lognormal(0, 0.25)
        has_steam = boro == "MANHATTAN" and rng.random() < 0.3
        has_oil = rng.random() < 0.1
        block, lot = rng.integers(1, 16000), rng.integers(1, 9000)
        bbl = f"{digit}{block:05d}{lot:04d}"
        if rng.random() < 0.03:
            bbl = f"{bbl};{digit}{block:05d}{lot + 1:04d}"
        if rng.random() < 0.01:
            bbl = NA
        bin_ = f"{digit}{rng.integers(0, 999_999):06d}" if rng.random() > 0.02 else f"{digit}999999"
        first_year = years[0] if rng.random() > 0.1 else years[1]
        for year in years:
            if year < first_year:
                continue
            eui = base_eui * rng.lognormal(0, 0.06)
            if rng.random() < anomaly_rate:
                eui *= rng.choice([0.3, 2.6])
            site_kbtu = eui * gfa
            wn_eui = eui * rng.normal(1.0, 0.02)
            steam_share = 0.35 if has_steam else 0.0
            oil_share = 0.2 if has_oil else 0.0
            elec_kbtu = site_kbtu * elec_share
            steam_kbtu = site_kbtu * steam_share
            oil_kbtu = site_kbtu * oil_share
            gas_kbtu = max(site_kbtu - elec_kbtu - steam_kbtu - oil_kbtu, 0.0)
            ghg = (
                elec_kbtu * TON_PER_KBTU["elec"]
                + gas_kbtu * TON_PER_KBTU["gas"]
                + steam_kbtu * TON_PER_KBTU["steam"]
                + oil_kbtu * TON_PER_KBTU["oil"]
            )
            missing_eui = rng.random() < 0.04
            rows.append(
                {
                    "report_year": str(year),
                    "property_id": str(pid),
                    "property_name": f"{rng.integers(1, 999)} Sample Street #{i}",
                    "parent_property_id": "Not Applicable: Standalone Property",
                    "year_ending": f"{year}-12-31T00:00:00.000",
                    "nyc_borough_block_and_lot": bbl,
                    "nyc_building_identification": bin_,
                    "address_1": f"{rng.integers(1, 999)} Sample Street",
                    "city": boro.title() if boro != "MANHATTAN" else "New York",
                    "postal_code": f"{rng.integers(10001, 11697)}",
                    "primary_property_type": ptype,
                    "largest_property_use_type": ptype,
                    "year_built": str(rng.integers(1890, 2020)),
                    "construction_status": "Existing",
                    "number_of_buildings": "1",
                    "occupancy": str(int(rng.choice([100, 100, 100, 95, 90]))),
                    "energy_star_score": str(int(rng.integers(1, 101)))
                    if scored and not missing_eui
                    else NA,
                    "site_eui_kbtu_ft": NA if missing_eui else _fmt(eui),
                    "weather_normalized_site_eui": NA
                    if missing_eui or rng.random() < 0.05
                    else _fmt(wn_eui),
                    "source_eui_kbtu_ft": NA if missing_eui else _fmt(eui * 1.6),
                    "weather_normalized_source": NA if missing_eui else _fmt(wn_eui * 1.6),
                    "site_energy_use_kbtu": NA if missing_eui else _fmt(site_kbtu),
                    "source_energy_use_kbtu": NA if missing_eui else _fmt(site_kbtu * 1.6),
                    "electricity_use_grid_purchase": NA if missing_eui else _fmt(elec_kbtu),
                    "electricity_use_grid_purchase_1": NA
                    if missing_eui
                    else _fmt(elec_kbtu / 3.412),
                    "natural_gas_use_kbtu": NA if missing_eui else _fmt(gas_kbtu),
                    "district_steam_use_kbtu": _fmt(steam_kbtu)
                    if has_steam and not missing_eui
                    else NA,
                    "fuel_oil_2_use_kbtu": _fmt(oil_kbtu) if has_oil and not missing_eui else NA,
                    "total_location_based_ghg": NA if missing_eui else _fmt(ghg),
                    "total_location_based_ghg_1": NA if missing_eui else _fmt(ghg * 1000 / gfa),
                    "water_use_all_water_sources": _fmt(gfa * rng.uniform(0.02, 0.08)),
                    "property_gfa_self_reported": _fmt(gfa, 0),
                    "property_gfa_calculated": _fmt(gfa, 0),
                    "report_submission_date": f"{year + 1}-04-{rng.integers(1, 28):02d}T10:00:00.000",
                    "borough": boro,
                    "latitude": f"{lat0 + rng.normal(0, 0.02):.6f}",
                    "longitude": f"{lon0 + rng.normal(0, 0.02):.6f}",
                }
            )
    df = pd.DataFrame(rows)

    # Quirks seen in the real extract.
    dupes = df.sample(n=max(1, len(df) // 200), random_state=seed).copy()
    dupes["report_submission_date"] = dupes["report_submission_date"].str.replace(
        "T10", "T09"
    )  # older submission
    dupes["site_eui_kbtu_ft"] = "999"
    test_row = df.iloc[[0]].copy()
    test_row["property_id"] = "1"
    test_row["construction_status"] = "Test"
    return pd.concat([df, dupes, test_row], ignore_index=True).fillna(NA)
