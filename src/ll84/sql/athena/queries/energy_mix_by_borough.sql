-- Share of site energy by source, per borough, for one year (partition-pruned).
SELECT p.borough,
       c.energy_source,
       round(sum(c.usage_kbtu) / 1e6, 1)                                            AS total_mmbtu,
       round(100.0 * sum(c.usage_kbtu) / sum(sum(c.usage_kbtu)) OVER (PARTITION BY p.borough), 1) AS pct_of_borough
FROM ${DATABASE}.consumption c
JOIN ${DATABASE}.properties p ON p.property_id = c.property_id
WHERE c.report_year = (SELECT max(report_year) FROM ${DATABASE}.annual_metrics)
  AND p.borough IS NOT NULL
GROUP BY p.borough, c.energy_source
ORDER BY p.borough, total_mmbtu DESC
