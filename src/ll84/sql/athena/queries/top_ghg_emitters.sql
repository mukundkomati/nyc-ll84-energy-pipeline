-- 25 largest emitters in the latest year, with intensity for context.
SELECT p.property_id,
       p.property_name,
       p.borough,
       m.primary_property_type,
       round(m.gross_floor_area_ft2)        AS gross_floor_area_ft2,
       round(m.ghg_emissions_tco2e)         AS ghg_tco2e,
       m.ghg_intensity_kgco2e_ft2,
       m.site_eui_kbtu_ft2
FROM ${DATABASE}.annual_metrics m
JOIN ${DATABASE}.properties p ON p.property_id = m.property_id
WHERE m.report_year = (SELECT max(report_year) FROM ${DATABASE}.annual_metrics)
  AND m.ghg_emissions_tco2e IS NOT NULL
ORDER BY m.ghg_emissions_tco2e DESC
LIMIT 25
