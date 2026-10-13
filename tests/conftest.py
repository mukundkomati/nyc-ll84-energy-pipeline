import pandas as pd
import pytest

from ll84.sample import make_sample


@pytest.fixture(scope="session")
def sample_raw() -> pd.DataFrame:
    return make_sample(n_properties=400, seed=7)


def metrics_frame(rows):
    """Build an annual_metrics-shaped frame from (property_id, year, type, site_eui[, wn_eui])."""
    records = []
    for r in rows:
        pid, year, ptype, site = r[:4]
        wn = r[4] if len(r) > 4 else None
        records.append(
            {
                "property_id": pid,
                "report_year": year,
                "primary_property_type": ptype,
                "site_eui_kbtu_ft2": site,
                "wn_site_eui_kbtu_ft2": wn,
            }
        )
    return pd.DataFrame(records).astype(
        {"site_eui_kbtu_ft2": "float64", "wn_site_eui_kbtu_ft2": "float64"}
    )
