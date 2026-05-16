# pitdb

<p align="center">
  <img src="assets/logo.png" alt="pitdb logo" width="180" />
</p>

**Point-in-time correct financial data for AI agents, built on kdb-x.**

AI agents working with market data have a fundamental problem: most data sources serve *current* information, not *historical knowledge*. An agent asking "what was AAPL's P/E ratio on November 3, 2024?" today gets a number calculated with data that wasn't available until weeks later. This is look-ahead bias — the agent reasons with information it couldn't have known at the queried moment.

pitdb solves this with a **bitemporal store**: every record carries two timestamps.

| Timestamp | Meaning |
|---|---|
| `event_time` | When the market event occurred |
| `knowledge_time` | When the data became publicly known |

All queries take both into account. `get_fundamentals("AAPL", as_of_event="2024-11-03", as_of_knowledge="2024-11-03")` returns only data that was *public* on November 3 — SEC filings after that date are invisible.

---

## Quickstart

```bash
pip install pitdb
```

```python
from pitdb import PitDB, ingest

# Launch a managed local kdb-x instance
db = PitDB.local()

# Ingest prices, corporate actions, and fundamentals
ingest(db, "AAPL:NASDAQ", start="2020-01-01")
db.save()

# Query — as of a specific knowledge date
prices = db.get_price_history(
    "AAPL:NASDAQ",
    start="2024-01-01",
    end="2024-11-03",
    as_of_date="2024-11-03",
)

# Context pack: single call, full reasoning bundle
pack = db.get_context_pack("AAPL:NASDAQ", as_of_date="2024-11-03")
# Returns: price_snapshot, trailing_30d_return, fundamentals, corporate_actions_90d
```

---

## The Problem

### Financial restatements

In March 2021, Virgin Galactic (SPCE) filed its FY2020 annual report with Q1 EPS = **-$0.30**. In May 2021, the SEC ruled that SPAC warrants must be reclassified as liabilities, forcing SPCE to file an amended 10-K/A. The restated Q1 EPS was **-$1.86** — a 6.2× larger loss.

A naive system today returns -$1.86 for all historical dates, including the 70-day window when analysts only had -$0.30. pitdb stores both filings with their EDGAR filing dates as `knowledge_time`:

```python
# Original 10-K filed 2021-03-01 — returns -$0.30
db.get_fundamentals("SPCE:NYSE", as_of_event="2020-03-31", as_of_knowledge="2021-04-15")

# Amended 10-K/A filed 2021-05-10 — returns -$1.86
db.get_fundamentals("SPCE:NYSE", as_of_event="2020-03-31", as_of_knowledge="2021-05-15")
```

### Late filing discovery

AAPL's Q3 FY2020 (period ended June 27, 2020) was filed with SEC EDGAR on July 31, 2020. The Q3 row has `knowledge_time = 2020-07-31`. Querying before that date returns no Q3 data — not because of filtering, but because the record simply didn't exist yet in the database's view of the world.

```python
# Returns None for Q3 — filing wasn't public yet
db.get_as_of("AAPL:NASDAQ", "eps", as_of_event="2020-06-27", as_of_knowledge="2020-07-30", table="fundamentals")

# Returns Q3 EPS — filing is now public
db.get_as_of("AAPL:NASDAQ", "eps", as_of_event="2020-06-27", as_of_knowledge="2020-08-01", table="fundamentals")
```

---

## Python API

### Connection

```python
db = PitDB.local()                    # managed local kdb-x in ~/.pitdb/data
db = PitDB.local(data_dir="/my/path") # custom data directory
db = PitDB("localhost", 5000)         # connect to external kdb-x instance
# For non-loopback hosts, opt in explicitly:
# db = PitDB("10.0.0.5", 5000, allow_insecure_remote=True)
```

### Ingestion

```python
from pitdb import ingest, ingest_prices, ingest_corporate_actions, ingest_fundamentals_edgar, ingest_fundamentals_sgx, ingest_earnings_history

ingest(db, "AAPL:NASDAQ", start="2020-01-01")          # routes automatically by exchange
ingest(db, "D05:SGX", start="2020-01-01")              # SGX — knowledge_time from yfinance earnings_dates (approximate announcement date)
ingest_prices(db, "AAPL:NASDAQ", start="2024-01-01")   # OHLCV only (exchange-aware suffix + close time)
ingest_corporate_actions(db, "MSFT:NYSE", start="2010-01-01")
ingest_fundamentals_edgar(db, "TSLA:NASDAQ")           # US equities — SEC EDGAR XBRL
ingest_fundamentals_sgx(db, "D05:SGX")                 # SGX equities — yfinance (announcement dates + values)
ingest_earnings_history(db, "TSLA:NASDAQ")             # EPS estimate vs actual (yfinance)

db.save()  # persist to disk
```

`knowledge_time` is derived from source metadata, not ingest time:
- **Prices** → market close time on the trading date (exchange-aware UTC: NYSE/NASDAQ = 21:00, SGX = 09:00, LSE = 16:00, TSE = 06:00, etc.)
- **US fundamentals** → SEC EDGAR filing date (10-K/10-Q `filed` field, including amendments)
- **SGX fundamentals** → yfinance earnings announcement date (approximate; see ADR 0003)
- **Corporate actions** → announcement date
- **Earnings history** → earnings announcement date

### Queries

```python
# Single value lookup
db.get_as_of("AAPL:NASDAQ", "close", as_of_event="2024-01-10", as_of_knowledge="2024-01-12")

# OHLCV series
db.get_price_history("AAPL:NASDAQ", start="2024-01-01", end="2024-11-03", as_of_date="2024-11-03")

# Fundamentals (EPS, P/E, revenue)
db.get_fundamentals("AAPL:NASDAQ", as_of_event="2024-09-30", as_of_knowledge="2024-11-15")

# Earnings history (estimate vs actual)
db.get_earnings_history("AAPL:NASDAQ", start="2022-01-01", end="2024-11-03", as_of_date="2024-11-03")

# Splits and dividends
db.get_corporate_actions("AAPL:NASDAQ", start="2020-01-01", end="2020-12-31", as_of_date="2020-12-31")

# Full reasoning bundle
db.get_context_pack("AAPL:NASDAQ", as_of_date="2024-11-03")

# Provenance audit trail — full ticker history
db.provenance("AAPL:NASDAQ")

# Provenance for a specific period-end date
db.provenance("AAPL:NASDAQ", event_time="2024-09-30")

# q escape hatch (disabled by default for safety)
# export PITDB_ENABLE_UNSAFE_QUERY=1
db.query("select from prices where ticker=`AAPL:NASDAQ")
# Or opt in at construction time:
# db = PitDB("localhost", 5000, allow_unsafe_query=True)
```

### Snapshot

Pin `knowledge_time` for a set of queries — useful when an agent issues multiple queries "as of the same moment":

```python
snap = db.snapshot("2024-11-03T21:00:00+00:00")
snap.get_price_history("AAPL:NASDAQ", "2024-01-01", "2024-11-03")
snap.get_fundamentals("AAPL:NASDAQ", "2024-09-30")
snap.get_context_pack("AAPL:NASDAQ")
```

### Walk

Iterate over market dates, yielding a fresh Snapshot at each step. The `knowledge_lag` parameter models realistic data arrival delays:

```python
from datetime import timedelta

for date, snap in db.walk("2024-01-01", "2024-12-31", knowledge_lag=timedelta(days=1)):
    pack = snap.get_context_pack("AAPL:NASDAQ")
    # ... agent reasoning here
```

---

## MCP Server (for Claude and other AI agents)

Start the pitdb MCP server so AI agents can query point-in-time correct data directly:

```bash
pitdb serve --data-dir ~/.pitdb/data
```

**Available tools:**

| Tool | Description |
|---|---|
| `get_price` | OHLCV for a single trading day |
| `get_price_history` | OHLCV series over a date range |
| `get_fundamentals` | EPS, P/E, revenue as of a date |
| `get_earnings_history` | EPS estimate vs actual across quarters |
| `get_context_pack` | Full reasoning bundle (recommended for agents) |
| `get_corporate_actions` | Splits and dividends over a range |
| `check_data_availability` | Record counts per table for a ticker/range |
| `list_tickers` | All instruments in the database |

All tools accept `as_of_date` — only data known on or before that date is returned.
For safety, MCP range tools enforce guardrails:
- `PITDB_MCP_MAX_QUERY_SPAN_DAYS` (default: `3650`)
- `PITDB_MCP_MAX_ROWS_PER_RESPONSE` (default: `10000`)

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pitdb": {
      "command": "pitdb",
      "args": ["serve"]
    }
  }
}
```

---

## CLI

```bash
# Ingest data for a ticker
pitdb ingest AAPL:NASDAQ --start 2020-01-01

# Query a context pack
pitdb query AAPL:NASDAQ --as-of 2024-11-03

# Query price history
pitdb query AAPL:NASDAQ --as-of 2024-11-03 --start 2024-01-01 --end 2024-11-03

# Start MCP server
pitdb serve --data-dir ~/.pitdb/data
```

---

## Schema

Five tables, all append-only:

| Table | Columns |
|---|---|
| `prices` | `event_time, knowledge_time, ticker, open, high, low, close, volume` |
| `fundamentals` | `event_time, knowledge_time, ticker, eps, pe_ratio, revenue` |
| `earnings_history` | `event_time, knowledge_time, ticker, period, eps_estimate, eps_actual, surprise_pct` |
| `corporate_actions` | `event_time, knowledge_time, ticker, action_type, factor` |
| `provenance` | `event_time, knowledge_time, ticker, table_name, source_name, source_url, source_timestamp` |

Every write is append-only. Corrections create new rows with a later `knowledge_time`; original rows are never modified.

---

## Data Sources

| Source | Data | Rate limit |
|---|---|---|
| yfinance (Yahoo Finance) | Prices, corporate actions, earnings history, SGX fundamentals (all exchanges) | No hard limit |
| SEC EDGAR XBRL API | EPS, revenue, filing dates for US equities (10-K/Q including amendments) | 10 req/s (free) |

Instruments are identified as `SYM:EXCHANGE` (e.g. `AAPL:NASDAQ`, `MSFT:NYSE`). Bare symbols default to NASDAQ.

**v1 covers US and SGX equities.** US fundamentals are sourced from SEC EDGAR; SGX fundamentals use yfinance (knowledge_time is approximate). Prices and corporate actions work for all exchanges with a yfinance symbol. Fundamentals for non-US, non-SGX exchanges are planned for a future release.

---

## Why kdb-x?

kdb-x is the industry-standard time-series database in finance. Its columnar storage and native `aj` (as-of join) make bitemporal queries a first-class operation — no extension needed. PyKX provides the Python bridge with a bundled Community Edition license.

See `docs/adr/` for architecture decision records.

---

## Development

```bash
git clone https://github.com/sudarshan2401/pitdb
cd pitdb
pip install -e ".[dev]"

# Unit tests (no network)
pytest tests/test_bitemporal.py

# Integration tests (hit live APIs)
pytest tests/test_ingest.py -m integration
```

---

## License

Apache-2.0. kdb-x Community Edition is subject to [KX's Community License](https://code.kx.com/insights/1.18/licensing/usage-restrictions.html#kdb-x-community-edition-download).
