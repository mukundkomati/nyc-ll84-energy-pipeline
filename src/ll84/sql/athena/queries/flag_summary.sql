-- Year-over-year EUI flags by year, property type and severity.
SELECT f.report_year,
       f.property_type,
       f.severity,
       count(*)                                  AS flagged_buildings,
       sum(CASE WHEN f.direction = 'increase' THEN 1 ELSE 0 END) AS increases,
       sum(CASE WHEN f.direction = 'decrease' THEN 1 ELSE 0 END) AS decreases,
       round(approx_percentile(abs(f.pct_change), 0.5) * 100, 0) AS median_abs_change_pct
FROM ${DATABASE}.eui_flags f
GROUP BY f.report_year, f.property_type, f.severity
ORDER BY f.report_year DESC, flagged_buildings DESC
