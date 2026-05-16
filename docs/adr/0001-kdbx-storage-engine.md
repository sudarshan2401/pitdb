# ADR 0001: kdb-x as the storage engine

## Status
Accepted

## Context
pitdb needs a time-series storage engine that can handle append-only bitemporal records and serve point-in-time correct queries efficiently. The core query pattern — "give me the value of X as known at time T, for event at time E" — maps directly to an as-of join on two time columns.

Alternatives considered:
- **DuckDB** — excellent analytical performance, easy to embed, pure Python install. No native as-of join; bitemporal queries require verbose window functions and are slower on large tick datasets.
- **PostgreSQL with temporal tables** — mature, well-understood, good temporal support via extensions. Requires a running server, no native as-of join semantics, slower on time-series at scale.
- **TimescaleDB** — purpose-built for time-series on top of PostgreSQL. Better than vanilla Postgres for this workload but still no native as-of join, and adds operational complexity.
- **kdb-x (kdb+ Community Edition)** — the industry-standard time-series database in capital markets. Native `aj` (as-of join) makes bitemporal queries a first-class operation. Columnar storage, nanosecond timestamps, and partition-by-date are built-in. kdb-x Community Edition is free.

## Decision
Use kdb-x as the storage engine via PyKX (KX's official Python-first API).

## Consequences
- **Positive:** `aj` makes bitemporal point-in-time queries trivial and fast. Schema design maps directly to finance industry conventions. Learning kdb-x is directly transferable to production finance environments where it is the dominant time-series database.
- **Negative:** Steep q language learning curve. Smaller open-source community than DuckDB or PostgreSQL. Requires kdb-x binary to be installed separately. Contributors unfamiliar with q will need onboarding.
- **Mitigation:** pitdb's Python API hides q entirely from end users. An escape hatch (`db.query(q_string)`) is available for contributors and power users who need it.
