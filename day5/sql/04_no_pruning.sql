-- AI tickets issued in the BSP week 16-22 Nov, filtered on ordinary COLUMNS (issue_date, airline_name).
-- Athena can't skip a folder for these: it opens every file under curated/ticket_issue/.
SELECT count(*) AS tickets, sum(fare_amount) AS fare_total
FROM ticket_issue
WHERE issue_date BETWEEN DATE '2026-11-16' AND DATE '2026-11-22'
  AND airline_name = 'Air India'
