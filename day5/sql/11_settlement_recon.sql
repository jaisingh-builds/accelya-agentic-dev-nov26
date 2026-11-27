-- Lab 4 (you write this, with your assistant): the SETTLEMENT RECONCILIATION view.
-- One row per ticket_number seen on EITHER side (tickets and remittance), with
--   ticket_number, airline_code, agency_iata, settlement_date, fare_amount, remitted_amount, status
-- status, checked in this order:
--   'UNKNOWN_TICKET'      remitted, but no such ticket in ticket_issue
--   'AMOUNT_MISMATCH'     remitted, but remitted_amount <> fare_amount
--   'MATCHED'             remitted, same amount (also when it was paid before it was due)
--   'NOT_DUE'             not remitted, and the ticket settles after the lab cut-off
--   'MISSING_REMITTANCE'  not remitted, and it was due on or before the lab cut-off
-- Lab cut-off = the latest remittance_date in the remittance table. Don't hard-code 2026-11-27.
-- Fixture assumption: ticket_number is unique on each side (606 tickets, 356 remittance lines),
-- so the FULL OUTER JOIN gives 608 rows: 606 tickets + 2 remittance-only lines. Module 10 reuses this view.
CREATE OR REPLACE VIEW settlement_recon AS
SELECT 'replace me' AS status
