# ADR 0002: Derive knowledge_time from source metadata, not ingest time

## Status
Accepted

## Context
Every bitemporal record in pitdb requires a `knowledge_time` — the timestamp representing when the data became publicly known. The obvious implementation is to stamp `knowledge_time = datetime.utcnow()` at the moment of ingestion.

The problem: if a user runs `pitdb ingest` today to backfill 5 years of historical data, every record gets `knowledge_time = today`. A query asking "what did we know about AAPL's EPS on 2021-11-01?" would correctly return the value, but the `knowledge_time` would falsely imply the data was only known today — breaking point-in-time correctness for any backtest or agent reasoning over the historical period.

## Decision
Derive `knowledge_time` from authoritative source metadata rather than ingest time:
- **Prices:** `knowledge_time = market close time on event_date` — EOD prices are public at market close.
- **Fundamentals:** `knowledge_time = SEC EDGAR filing date` — financial statements become public at the moment of EDGAR submission, which is recorded in EDGAR's API response.
- **Corporate actions:** `knowledge_time = announcement date` sourced from the data provider.

The actual ingest timestamp is recorded separately in the Provenance Record but is not used as `knowledge_time`.

## Consequences
- **Positive:** Historical data is bitemporal-correct from initial ingestion. Backtests and agent queries over historical periods reflect what was actually knowable at the time. Provenance records make this auditable.
- **Negative:** pitdb must correctly parse and trust source timestamps. If a source provides an incorrect filing date, pitdb will propagate that error into `knowledge_time`. This is a data quality dependency on upstream sources.
- **Mitigation:** The Provenance Record stores source name, URL, and raw source timestamp alongside every derived `knowledge_time`, making errors detectable and correctable without reingesting from scratch.
