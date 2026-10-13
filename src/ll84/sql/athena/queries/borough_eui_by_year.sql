-- Median and floor-area-weighted site EUI, total GHG, by borough and year.
SELECT p.borough,
       m.report_year,
       count(*)                                                   AS buildings,
       round(approx_percentile(m.site_eui_kbtu_ft2, 0.5), 1)      AS median_site_eui,
       round(sum(m.site_energy_kbtu) / sum(m.gross_floor_area_ft2), 1) AS weighted_site_eui,
       round(sum(m.ghg_emissions_tco2e))                          AS total_ghg_tco2e
FROM ${DATABASE}.annual_metrics m
JOIN ${DATABASE}.properties p ON p.property_id = m.property_id
WHERE m.site_eui_kbtu_ft2 > 0
  AND m.gross_floor_area_ft2 > 0
  AND p.borough IS NOT NULL
GROUP BY p.borough, m.report_year
ORDER BY m.report_year, p.borough
