-- EUI distribution by property type for the latest year (types with 50+ buildings).
WITH latest AS (
    SELECT max(report_year) AS yr FROM ${DATABASE}.annual_metrics
)
SELECT m.primary_property_type,
       count(*)                                                       AS buildings,
       round(approx_percentile(m.site_eui_kbtu_ft2, 0.25), 1)         AS p25_site_eui,
       round(approx_percentile(m.site_eui_kbtu_ft2, 0.50), 1)         AS median_site_eui,
       round(approx_percentile(m.site_eui_kbtu_ft2, 0.75), 1)         AS p75_site_eui,
       round(avg(m.energy_star_score), 0)                             AS avg_energy_star
FROM ${DATABASE}.annual_metrics m, latest
WHERE m.report_year = latest.yr
  AND m.site_eui_kbtu_ft2 > 0
GROUP BY m.primary_property_type
HAVING count(*) >= 50
ORDER BY buildings DESC
