# ADR 0003: Provenance for non-US fundamental records

## Status
Accepted

## Context

For US equities, SEC EDGAR is the single authoritative source for both the `knowledge_time` of a fundamental record (the `filed` date) and the data values (EPS, revenue from XBRL). One provenance row per record is sufficient.

For non-US equities (e.g. SGX), there is no free public API equivalent to EDGAR that provides both structured financial data and authoritative filing dates without browser session authentication. The SGX announcements API (`api.sgx.com/announcements/v1.1/company`) requires a browser session cookie established by the SGX website — it cannot be called directly as an unauthenticated REST API.

## Decision

For SGX fundamentals, use **yfinance as the single source** for both `knowledge_time` and data values:

- `earnings_dates` — provides historical earnings announcement dates (used as `knowledge_time`) and Reported EPS
- `quarterly_income_stmt` — provides Revenue and Basic EPS by period-end date (used as `event_time`)

`knowledge_time` is derived by matching each period-end date to the earliest announcement date on or after it from `earnings_dates`. This is a single provenance row per record (yfinance).

**Caveat:** yfinance announcement dates reflect when data became available in Yahoo Finance's system, not the exact SGX regulatory filing timestamp. This is an approximation, not the authoritative regulatory record.

## Alternatives considered

**SGX announcements API** — the correct authoritative source, but requires browser session authentication. Not callable as a plain HTTP request. Would require Selenium/Playwright to establish a session first, adding a heavy dependency incompatible with pitdb's lightweight positioning.

**Dual provenance (two rows per record)** — originally proposed when the plan was SGX API for `knowledge_time` + yfinance for values. Moot now that both come from yfinance. The dual-provenance pattern remains available for future use if an authoritative non-US filing date source is integrated.

## Consequences

- SGX fundamentals ingest is free, unauthenticated, and has no browser dependency
- `knowledge_time` for SGX records is approximate (yfinance announcement date, not official SGX filing timestamp)
- Single provenance row per record, source_name = "yfinance"
- If the SGX API becomes freely accessible in future, `ingest_fundamentals_sgx` can be updated to use it for `knowledge_time` with dual provenance per the original design
