# Phase 5C operational index decision

No Phase 5C migration was retained.

PostgreSQL `EXPLAIN` on the local beta dataset (2,109 `scan_urls`) showed that expired-lease and
due-retry diagnostics already use `ix_scan_urls_status_lease_expires` and
`ix_scan_urls_status_next_retry`. The oldest-queued query also used the existing status/lease
index. The recent throughput aggregate scanned 53 shared buffers and completed in 2.154 ms; the
failure distribution used the existing status index and completed in 0.402 ms. Active-job age used
a sequential scan because the job table is small.

The proposed partial age/completion indexes therefore had no demonstrated benefit on the intended
bounded queries. Adding their write and storage cost would be premature. Re-evaluate with production
cardinality and representative `EXPLAIN (ANALYZE, BUFFERS)` evidence before adding a migration.
