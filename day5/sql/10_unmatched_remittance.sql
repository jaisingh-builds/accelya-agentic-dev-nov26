-- Lab 4 (you write this, with your assistant): UNMATCHED REMITTANCE for settlement 2026-11-27.
-- A remittance line is unmatched when
--   (a) no curated ticket has its ticket_number          -> reason 'UNKNOWN_TICKET'
--   (b) the ticket exists but remitted_amount <> fare_amount -> reason 'AMOUNT_MISMATCH'
-- Output columns, in this order:
--   ticket_number, agency_iata, remitted_amount, fare_amount (NULL when unknown), reason
-- ORDER BY ticket_number. Tables: remittance, ticket_issue. Check: python3 day5/scripts/check_recon.py
SELECT 'replace me' AS ticket_number
