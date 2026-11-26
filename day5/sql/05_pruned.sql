-- The same tickets ON THIS FIXTURE, filtered on the PARTITION columns: that week settles on 27 Nov.
-- Athena reads one folder: settlement_date=2026-11-27/airline_code=AI/. Same result on this fixture, fewer bytes.
-- (They agree because the lab derives settlement_date from issue_date with one weekly rule; on other data they may not.)
SELECT count(*) AS tickets, sum(fare_amount) AS fare_total
FROM ticket_issue
WHERE settlement_date = DATE '2026-11-27'
  AND airline_code = 'AI'
