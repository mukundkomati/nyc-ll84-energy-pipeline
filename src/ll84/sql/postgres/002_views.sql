-- Reporting views over the LL84 model.

-- Open review items with building context, worst first.
CREATE OR REPLACE VIEW ll84.v_open_eui_flags AS
SELECT r.review_id,
       r.property_id,
       p.property_name,
       p.address,
       p.borough,
       r.property_type,
       r.prior_year,
       r.report_year,
       r.eui_metric,
       r.prior_eui,
       r.current_eui,
       round((r.pct_change * 100)::numeric, 1) AS pct_change,
       round(r.peer_z_score::numeric, 2)      AS peer_z_score,
       r.reasons,
       r.severity,
       r.first_flagged_at
FROM ll84.eui_review_log r
JOIN ll84.properties p USING (property_id)
WHERE r.status = 'open'
ORDER BY (r.severity = 'high') DESC, abs(r.pct_change) DESC;

-- Borough x year summary.
CREATE OR REPLACE VIEW ll84.v_borough_year_summary AS
SELECT p.borough,
       m.report_year,
       count(*)                                                        AS buildings,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY m.site_eui_kbtu_ft2) AS median_site_eui,
       sum(m.site_energy_kbtu) / NULLIF(sum(m.gross_floor_area_ft2), 0) AS area_weighted_site_eui,
       sum(m.ghg_emissions_tco2e)                                       AS total_ghg_tco2e,
       avg(m.energy_star_score)                                         AS avg_energy_star_score
FROM ll84.annual_metrics m
JOIN ll84.properties p USING (property_id)
WHERE m.site_eui_kbtu_ft2 > 0
GROUP BY p.borough, m.report_year;

-- Fuel mix: share of site energy by source per year.
CREATE OR REPLACE VIEW ll84.v_energy_mix AS
SELECT c.report_year,
       mt.energy_source,
       sum(c.usage_kbtu)                                                              AS total_kbtu,
       sum(c.usage_kbtu) / NULLIF(sum(sum(c.usage_kbtu)) OVER (PARTITION BY c.report_year), 0) AS share
FROM ll84.consumption c
JOIN ll84.meters mt USING (meter_id)
GROUP BY c.report_year, mt.energy_source;
