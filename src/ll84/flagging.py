"""Year-over-year EUI anomaly detection.

For every property with reports in two consecutive years, compare energy use intensity
(weather-normalized site EUI when both years have it, otherwise site EUI) and flag when
the change falls outside the expected range. Three independent rules:

  yoy_pct_change   |change| >= pct_threshold (default 50%)
  peer_outlier     the log change is extreme versus same-type buildings that year
                   (robust z-score using median/MAD, |z| >= z_threshold)
  moved_out_of_range
                   EUI sat inside its peer group's range last year and is outside it now
                   (range = Q1 - k*IQR .. Q3 + k*IQR within property type and year)

Peer rules only apply when the peer group has at least `min_peer_group` buildings.
Every flagged building is written to the review log for a human to triage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ll84.logging_setup import FLAG_LOGGER

log = logging.getLogger(__name__)
flag_log = logging.getLogger(FLAG_LOGGER)

FLAG_COLUMNS = [
    "property_id",
    "report_year",
    "prior_year",
    "eui_metric",
    "property_type",
    "prior_eui",
    "current_eui",
    "pct_change",
    "peer_z_score",
    "peer_low",
    "peer_high",
    "direction",
    "reasons",
    "severity",
    "flagged_at",
]


@dataclass(frozen=True)
class FlagConfig:
    pct_threshold: float = 0.5
    z_threshold: float = 3.5
    iqr_multiplier: float = 3.0
    min_peer_group: int = 20


def _pair_consecutive_years(metrics: pd.DataFrame) -> pd.DataFrame:
    m = metrics[
        [
            "property_id",
            "report_year",
            "primary_property_type",
            "wn_site_eui_kbtu_ft2",
            "site_eui_kbtu_ft2",
        ]
    ]
    m = m.sort_values(["property_id", "report_year"]).copy()
    grouped = m.groupby("property_id")
    m["prior_year"] = grouped["report_year"].shift(1)
    m["prior_type"] = grouped["primary_property_type"].shift(1)
    m["prior_wn"] = grouped["wn_site_eui_kbtu_ft2"].shift(1)
    m["prior_site"] = grouped["site_eui_kbtu_ft2"].shift(1)
    m = m[(m["report_year"] - m["prior_year"]) == 1].copy()

    # Compare like with like: both years must have the same metric, and it must be > 0.
    use_wn = (m["wn_site_eui_kbtu_ft2"] > 0) & (m["prior_wn"] > 0)
    use_site = ~use_wn & (m["site_eui_kbtu_ft2"] > 0) & (m["prior_site"] > 0)
    m["eui_metric"] = np.where(
        use_wn, "weather_normalized_site_eui", np.where(use_site, "site_eui", None)
    )
    m["current_eui"] = np.where(
        use_wn, m["wn_site_eui_kbtu_ft2"], np.where(use_site, m["site_eui_kbtu_ft2"], np.nan)
    )
    m["prior_eui"] = np.where(use_wn, m["prior_wn"], np.where(use_site, m["prior_site"], np.nan))
    m = m[m["eui_metric"].notna()].copy()
    m["prior_year"] = m["prior_year"].astype("int64")
    m["pct_change"] = m["current_eui"] / m["prior_eui"] - 1.0
    m["log_ratio"] = np.log(m["current_eui"] / m["prior_eui"])
    return m


def _peer_ranges(metrics: pd.DataFrame, cfg: FlagConfig) -> pd.DataFrame:
    """Per (property type, year, metric): Tukey-style range on EUI level."""
    long = pd.concat(
        [
            metrics[["primary_property_type", "report_year"]].assign(
                eui_metric="weather_normalized_site_eui", eui=metrics["wn_site_eui_kbtu_ft2"]
            ),
            metrics[["primary_property_type", "report_year"]].assign(
                eui_metric="site_eui", eui=metrics["site_eui_kbtu_ft2"]
            ),
        ],
        ignore_index=True,
    )
    long = long[(long["eui"] > 0) & long["primary_property_type"].notna()]
    stats = (
        long.groupby(["primary_property_type", "report_year", "eui_metric"])["eui"]
        .agg(n="count", q1=lambda s: s.quantile(0.25), q3=lambda s: s.quantile(0.75))
        .reset_index()
    )
    iqr = stats["q3"] - stats["q1"]
    stats["low"] = (stats["q1"] - cfg.iqr_multiplier * iqr).clip(lower=0)
    stats["high"] = stats["q3"] + cfg.iqr_multiplier * iqr
    stats.loc[stats["n"] < cfg.min_peer_group, ["low", "high"]] = np.nan
    return stats[["primary_property_type", "report_year", "eui_metric", "low", "high"]]


def _robust_z(pairs: pd.DataFrame, cfg: FlagConfig) -> pd.Series:
    keys = ["primary_property_type", "report_year", "eui_metric"]
    grp = pairs.groupby(keys)["log_ratio"]
    median = grp.transform("median")
    mad = (pairs["log_ratio"] - median).abs().groupby([pairs[k] for k in keys]).transform("median")
    count = grp.transform("count")
    z = 0.6745 * (pairs["log_ratio"] - median) / mad.replace(0, np.nan)
    return z.where(count >= cfg.min_peer_group)


def flag_eui_changes(
    metrics: pd.DataFrame, cfg: FlagConfig | None = None, now: datetime | None = None
) -> pd.DataFrame:
    """Return one row per flagged property-year, ready for the review log."""
    cfg = cfg or FlagConfig()
    now = now or datetime.now(timezone.utc)
    pairs = _pair_consecutive_years(metrics)
    if pairs.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)

    pairs["peer_z_score"] = _robust_z(pairs, cfg)

    ranges = _peer_ranges(metrics, cfg)
    pairs = pairs.merge(
        ranges, on=["primary_property_type", "report_year", "eui_metric"], how="left"
    )
    prior_ranges = ranges.rename(
        columns={
            "primary_property_type": "prior_type",
            "report_year": "prior_year",
            "low": "prior_low",
            "high": "prior_high",
        }
    )
    pairs = pairs.merge(prior_ranges, on=["prior_type", "prior_year", "eui_metric"], how="left")

    rule_pct = pairs["pct_change"].abs() >= cfg.pct_threshold
    rule_peer = pairs["peer_z_score"].abs() >= cfg.z_threshold
    was_inside = pairs["prior_eui"].between(pairs["prior_low"], pairs["prior_high"])
    now_outside = (pairs["current_eui"] < pairs["low"]) | (pairs["current_eui"] > pairs["high"])
    rule_range = was_inside & now_outside & pairs["low"].notna()

    reasons = pd.Series([[] for _ in range(len(pairs))], index=pairs.index)
    for name, mask in (
        ("yoy_pct_change", rule_pct),
        ("peer_outlier", rule_peer),
        ("moved_out_of_range", rule_range),
    ):
        for idx in pairs.index[mask.fillna(False)]:
            reasons.at[idx] = reasons.at[idx] + [name]
    pairs["reasons"] = reasons
    flagged = pairs[pairs["reasons"].map(len) > 0].copy()
    if flagged.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)

    big_swing = flagged["log_ratio"].abs() >= np.log(2)  # doubled or halved
    flagged["severity"] = np.where((flagged["reasons"].map(len) >= 2) | big_swing, "high", "medium")
    flagged["direction"] = np.where(flagged["pct_change"] > 0, "increase", "decrease")
    flagged["reasons"] = flagged["reasons"].map(",".join)
    flagged["property_type"] = flagged["primary_property_type"]
    flagged["peer_low"] = flagged["low"]
    flagged["peer_high"] = flagged["high"]
    ts = pd.Timestamp(now)
    flagged["flagged_at"] = ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts
    out = flagged[FLAG_COLUMNS].sort_values(["report_year", "property_id"]).reset_index(drop=True)
    for col in ("prior_eui", "current_eui", "pct_change", "peer_z_score", "peer_low", "peer_high"):
        out[col] = out[col].astype("float64").round(4)
    return out


def log_flags(flags: pd.DataFrame) -> None:
    """Write one review line per flagged building (logs/eui_flags.log + console)."""
    for row in flags.itertuples(index=False):
        flag_log.warning(
            "EUI_FLAG property_id=%s year=%s prior_year=%s type=%r metric=%s eui %.1f -> %.1f (%+.0f%%) "
            "peer_z=%s severity=%s reasons=%s",
            row.property_id,
            row.report_year,
            row.prior_year,
            row.property_type,
            row.eui_metric,
            row.prior_eui,
            row.current_eui,
            row.pct_change * 100,
            "n/a" if pd.isna(row.peer_z_score) else f"{row.peer_z_score:.1f}",
            row.severity,
            row.reasons,
        )
    summary = flags.groupby(["report_year", "severity"]).size().to_dict() if len(flags) else {}
    log.info("Flagged %d property-years for review %s", len(flags), summary)
