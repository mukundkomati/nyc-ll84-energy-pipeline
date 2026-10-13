import numpy as np
import pytest

from ll84.flagging import FlagConfig, flag_eui_changes
from ll84.sample import make_sample
from ll84.transform import transform

from .conftest import metrics_frame

NO_PEERS = FlagConfig(pct_threshold=0.5, min_peer_group=10_000)  # isolate the % rule


def test_flags_large_increase_and_decrease():
    m = metrics_frame(
        [
            (1, 2022, "Office", 80.0),
            (1, 2023, "Office", 130.0),
            (2, 2022, "Office", 80.0),
            (2, 2023, "Office", 30.0),
            (3, 2022, "Office", 80.0),
            (3, 2023, "Office", 95.0),
        ]
    )
    flags = flag_eui_changes(m, NO_PEERS).set_index("property_id")
    assert set(flags.index) == {1, 2}
    assert flags.loc[1, "direction"] == "increase"
    assert flags.loc[2, "direction"] == "decrease"
    assert flags.loc[1, "pct_change"] == pytest.approx(0.625)
    assert flags.loc[2, "severity"] == "high"  # more than halved
    assert flags.loc[1, "reasons"] == "yoy_pct_change"


def test_only_consecutive_years_are_compared():
    m = metrics_frame([(1, 2022, "Office", 50.0), (1, 2024, "Office", 150.0)])
    assert flag_eui_changes(m, NO_PEERS).empty


def test_prefers_weather_normalized_when_both_years_have_it():
    m = metrics_frame([(1, 2022, "Office", 80.0, 80.0), (1, 2023, "Office", 140.0, 85.0)])
    assert flag_eui_changes(m, NO_PEERS).empty  # weather explains it

    m = metrics_frame([(1, 2022, "Office", 80.0, None), (1, 2023, "Office", 140.0, 85.0)])
    flags = flag_eui_changes(m, NO_PEERS)
    assert flags.iloc[0]["eui_metric"] == "site_eui"  # falls back, never mixes metrics


def test_zero_or_missing_eui_is_not_compared():
    m = metrics_frame(
        [
            (1, 2022, "Office", 0.0),
            (1, 2023, "Office", 90.0),
            (2, 2022, "Office", np.nan),
            (2, 2023, "Office", 90.0),
        ]
    )
    assert flag_eui_changes(m, NO_PEERS).empty


def _peer_group(n=40, spike_pid=None, spike=1.0, base_level=80.0):
    rng = np.random.default_rng(0)
    rows = []
    for pid in range(n):
        level = base_level * rng.lognormal(0, 0.15)
        change = rng.normal(1.0, 0.03)
        if pid == spike_pid:
            change = spike
        rows += [(pid, 2022, "Office", level), (pid, 2023, "Office", level * change)]
    return metrics_frame(rows)


def test_peer_outlier_catches_moderate_change_in_a_stable_group():
    cfg = FlagConfig(pct_threshold=0.9, z_threshold=3.5, min_peer_group=20)
    flags = flag_eui_changes(_peer_group(spike_pid=5, spike=1.35), cfg)
    assert list(flags["property_id"]) == [5]
    assert "peer_outlier" in flags.iloc[0]["reasons"]
    assert "yoy_pct_change" not in flags.iloc[0]["reasons"]


def test_peer_rules_need_a_big_enough_group():
    cfg = FlagConfig(pct_threshold=0.9, min_peer_group=50)
    assert flag_eui_changes(_peer_group(n=40, spike_pid=5, spike=1.35), cfg).empty


def test_moved_out_of_range():
    m = _peer_group(n=40)
    # Property 0 sits at the median in 2022 and jumps far above the peer range in 2023.
    m.loc[(m.property_id == 0) & (m.report_year == 2022), "site_eui_kbtu_ft2"] = 80.0
    m.loc[(m.property_id == 0) & (m.report_year == 2023), "site_eui_kbtu_ft2"] = 400.0
    flags = flag_eui_changes(m, FlagConfig(min_peer_group=20)).set_index("property_id")
    assert "moved_out_of_range" in flags.loc[0, "reasons"]
    assert flags.loc[0, "severity"] == "high"
    assert flags.loc[0, "current_eui"] > flags.loc[0, "peer_high"]


def test_sample_data_recovers_planted_anomalies():
    tables = transform(make_sample(n_properties=600, seed=11, anomaly_rate=0.03))
    flags = flag_eui_changes(tables.annual_metrics, FlagConfig())
    assert len(flags) > 0
    assert flags["pct_change"].abs().min() > 0.1
    assert flags[["property_id", "report_year"]].duplicated().sum() == 0
