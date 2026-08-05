"""Ingestion pipeline — watched folder, activity-file parsing, stream storage.

Sits above ``app.services``: it orchestrates parsing and persistence of
incoming recordings but is never imported by them.

Filled in by WP-4 (watched folder scan, FIT/GPX/TCX parsing, sessions &
recordings, per-second stream parquet files, quarantine handling).
"""
