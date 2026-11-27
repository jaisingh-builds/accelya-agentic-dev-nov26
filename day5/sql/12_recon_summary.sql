-- The settlement summary a finance reviewer signs off: one line per status.
SELECT status, count(*) AS tickets, sum(fare_amount) AS fare_total, sum(remitted_amount) AS remitted_total
FROM settlement_recon
GROUP BY status
ORDER BY status
