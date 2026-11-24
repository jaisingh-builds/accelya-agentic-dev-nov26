# M3 Lab 1 - raw zone ingest: evidence

**Learner:** `<your-id>`   **Bucket:** `accelya-m1-<you>-<acct>`

## 1. Upload to raw/ (the trigger fires)
- correlation_id you set: `<your-id>-d3-001`
- Lambda log line (`file.received`): paste ONE line - keys and counts only
- record_count reported: `__` (the file has 42 data rows)

## 2. Upload to the bucket root (the filter at work)
- Did the Lambda run? `yes / no` - why?

## 3. Same file to raw/ again (a duplicate event)
- ETag first upload: `____`   ETag second upload: `____`   same? `yes / no`
- How many `file.received` lines now? `__`
- What would a processor that writes to validated/ do with the second one, today? What must it do instead?

## 4. The zone layout you'd design
| Zone | Prefix | Who writes | Who reads | Kept for | Why it exists |
|---|---|---|---|---|---|
| raw | `raw/` | | | | |
| validated | | | | | |
| quarantine | | | | | |
| curated | | | | | |
