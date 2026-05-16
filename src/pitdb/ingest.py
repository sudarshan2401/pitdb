from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pandas as pd
import requests
import yfinance as yf

if TYPE_CHECKING:
    from .connection import PitDB

EDGAR_HEADERS = {"User-Agent": "pitdb/0.1 sudarshan.k@u.nus.edu"}

# Per-exchange yfinance suffix and approximate UTC market close hour.
# close_hour ignores DST — sufficient for date-level knowledge_time accuracy.
_EXCHANGE: dict[str, dict] = {
    "NYSE":    {"suffix": "",     "close_hour": 21},
    "NASDAQ":  {"suffix": "",     "close_hour": 21},
    "AMEX":    {"suffix": "",     "close_hour": 21},
    "OTC":     {"suffix": "",     "close_hour": 21},
    "TSX":     {"suffix": ".TO",  "close_hour": 21},
    "TSXV":    {"suffix": ".V",   "close_hour": 21},
    "LSE":     {"suffix": ".L",   "close_hour": 16},
    "SGX":     {"suffix": ".SI",  "close_hour": 9},
    "ASX":     {"suffix": ".AX",  "close_hour": 6},
    "HKEX":    {"suffix": ".HK",  "close_hour": 8},
    "TSE":     {"suffix": ".T",   "close_hour": 6},
    "NSE":     {"suffix": ".NS",  "close_hour": 10},
    "BSE":     {"suffix": ".BO",  "close_hour": 10},
    "SSE":     {"suffix": ".SS",  "close_hour": 7},
    "SZSE":    {"suffix": ".SZ",  "close_hour": 7},
    "KRX":     {"suffix": ".KS",  "close_hour": 6},
    "EPA":     {"suffix": ".PA",  "close_hour": 16},
    "AMS":     {"suffix": ".AS",  "close_hour": 16},
    "FRA":     {"suffix": ".F",   "close_hour": 16},
    "XETRA":   {"suffix": ".DE",  "close_hour": 16},
    "BME":     {"suffix": ".MC",  "close_hour": 16},
    "SIX":     {"suffix": ".SW",  "close_hour": 16},
    "TWSE":    {"suffix": ".TW",  "close_hour": 6},
    "SET":     {"suffix": ".BK",  "close_hour": 10},
    "IDX":     {"suffix": ".JK",  "close_hour": 9},
    "BOVESPA": {"suffix": ".SA",  "close_hour": 21},
    "BCBA":    {"suffix": ".BA",  "close_hour": 21},
    "BMV":     {"suffix": ".MX",  "close_hour": 21},
    "CPH":     {"suffix": ".CO",  "close_hour": 16},
}

_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "OTC"}


def _yf_suffix(exchange: str) -> str:
    return _EXCHANGE.get(exchange, {}).get("suffix", "")


def _close_hour(exchange: str) -> int:
    return _EXCHANGE.get(exchange, {}).get("close_hour", 21)


def _resolve_ticker(ticker: str) -> tuple[str, str]:
    if ":" in ticker:
        sym, exchange = ticker.split(":", 1)
        return sym, exchange
    return ticker, "NASDAQ"


def _to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def _to_event_ts(ts) -> pd.Timestamp:
    # Nanoseconds zeroed because kdb-x timestamp equality is exact.
    return _to_utc(ts).replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)


def _to_float(v) -> float:
    try:
        f = float(v)
        return f if f == f else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def ingest_prices(db: "PitDB", ticker: str, start: str, end: str | None = None):
    """knowledge_time = market close time on each trading date."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _yf_suffix(exchange)
    close_hour = _close_hour(exchange)

    raw = yf.download(yf_symbol, start=start, end=end, auto_adjust=False, progress=False)
    if raw.empty:
        return

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    rows = []
    for date, row in raw.iterrows():
        date_ts = _to_event_ts(date)
        knowledge_ts = date_ts.replace(hour=close_hour, minute=0, second=0)
        rows.append({
            "event_time": date_ts,
            "knowledge_time": knowledge_ts,
            "ticker": qualified,
            "open": float(row.get("open", float("nan"))),
            "high": float(row.get("high", float("nan"))),
            "low": float(row.get("low", float("nan"))),
            "close": float(row.get("close", float("nan"))),
            "volume": int(row.get("volume", 0)),
        })
        _write_provenance(db, qualified, date_ts, knowledge_ts,
                          table_name="prices",
                          source_name="yfinance",
                          source_url=f"https://finance.yahoo.com/quote/{yf_symbol}",
                          source_timestamp=knowledge_ts)

    db._append_df("prices", pd.DataFrame(rows))


def ingest_corporate_actions(db: "PitDB", ticker: str, start: str, end: str | None = None):
    """knowledge_time = announcement date."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _yf_suffix(exchange)
    t = yf.Ticker(yf_symbol)

    splits = t.splits
    if splits is not None and not splits.empty:
        for date, factor in splits.items():
            date_ts = _to_utc(date)
            _write_corporate_action(db, qualified, date_ts, date_ts, "split", float(factor))
            _write_provenance(db, qualified, date_ts, date_ts,
                              table_name="corporate_actions",
                              source_name="yfinance",
                              source_url=f"https://finance.yahoo.com/quote/{yf_symbol}",
                              source_timestamp=date_ts)

    dividends = t.dividends
    if dividends is not None and not dividends.empty:
        for date, amount in dividends.items():
            date_ts = _to_utc(date)
            _write_corporate_action(db, qualified, date_ts, date_ts, "dividend", float(amount))
            _write_provenance(db, qualified, date_ts, date_ts,
                              table_name="corporate_actions",
                              source_name="yfinance",
                              source_url=f"https://finance.yahoo.com/quote/{yf_symbol}",
                              source_timestamp=date_ts)


def ingest_fundamentals_edgar(db: "PitDB", ticker: str):
    """US equities only. knowledge_time = SEC EDGAR filing date."""
    sym, _ = _resolve_ticker(ticker)
    cik = _get_cik(sym)
    if cik is None:
        return

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    resp = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
    if resp.status_code != 200:
        return

    facts = resp.json().get("facts", {}).get("us-gaap", {})
    rows = []

    eps_data = _extract_concept(facts, "EarningsPerShareBasic")
    revenue_data = (
        _extract_concept(facts, "Revenues")
        or _extract_concept(facts, "RevenueFromContractWithCustomerExcludingAssessedTax")
    )

    for item in eps_data:
        event_ts = pd.Timestamp(item["end"], tz="UTC")
        knowledge_ts = pd.Timestamp(item["filed"], tz="UTC")
        rows.append({
            "event_time": event_ts,
            "knowledge_time": knowledge_ts,
            "ticker": ticker,
            "eps": item["val"],
            "pe_ratio": float("nan"),
            "revenue": float("nan"),
        })
        _write_provenance(db, ticker, event_ts, knowledge_ts,
                          table_name="fundamentals",
                          source_name="SEC EDGAR",
                          source_url=url,
                          source_timestamp=knowledge_ts)

    for item in revenue_data:
        event_ts = pd.Timestamp(item["end"], tz="UTC")
        knowledge_ts = pd.Timestamp(item["filed"], tz="UTC")
        rows.append({
            "event_time": event_ts,
            "knowledge_time": knowledge_ts,
            "ticker": ticker,
            "eps": float("nan"),
            "pe_ratio": float("nan"),
            "revenue": item["val"],
        })
        _write_provenance(db, ticker, event_ts, knowledge_ts,
                          table_name="fundamentals",
                          source_name="SEC EDGAR",
                          source_url=url,
                          source_timestamp=knowledge_ts)

    if rows:
        db._append_df("fundamentals", pd.DataFrame(rows))

    time.sleep(0.15)  # respect EDGAR's 10 req/s limit


def ingest_fundamentals_sgx(db: "PitDB", ticker: str):
    """SGX equities. knowledge_time = yfinance earnings announcement date (approximate —
    reflects Yahoo Finance availability, not the official SGX filing timestamp)."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _yf_suffix(exchange)
    t = yf.Ticker(yf_symbol)
    yf_url = f"https://finance.yahoo.com/quote/{yf_symbol}"

    try:
        ed = t.earnings_dates
    except Exception:
        ed = None

    announcement_dates: list[pd.Timestamp] = []
    eps_by_announcement: dict[pd.Timestamp, float] = {}
    if ed is not None and not ed.empty:
        for ann_ts, row in ed.iterrows():
            ann_day = _to_event_ts(ann_ts)
            announcement_dates.append(ann_day)
            reported = row.get("Reported EPS")
            if reported is not None and reported == reported:
                eps_by_announcement[ann_day] = float(reported)
        announcement_dates = sorted(announcement_dates)

    try:
        qi = t.quarterly_income_stmt
    except Exception:
        qi = None

    if qi is None or qi.empty:
        return

    rows = []
    for period_end in qi.columns:
        event_ts = _to_event_ts(period_end)
        knowledge_ts = _match_announcement_date(event_ts, announcement_dates) or event_ts

        # Prefer income_stmt Basic EPS; fall back to earnings_dates Reported EPS.
        eps = float("nan")
        if "Basic EPS" in qi.index:
            eps = _to_float(qi.loc["Basic EPS", period_end])
        if (eps != eps) and knowledge_ts in eps_by_announcement:
            eps = eps_by_announcement[knowledge_ts]

        revenue = float("nan")
        for label in ("Total Revenue", "Revenue"):
            if label in qi.index:
                revenue = _to_float(qi.loc[label, period_end])
                break

        rows.append({
            "event_time": event_ts,
            "knowledge_time": knowledge_ts,
            "ticker": qualified,
            "eps": eps,
            "pe_ratio": float("nan"),
            "revenue": revenue,
        })
        _write_provenance(db, qualified, event_ts, knowledge_ts,
                          table_name="fundamentals",
                          source_name="yfinance",
                          source_url=yf_url,
                          source_timestamp=knowledge_ts)

    if rows:
        db._append_df("fundamentals", pd.DataFrame(rows))


def ingest_earnings_history(db: "PitDB", ticker: str):
    """knowledge_time = earnings announcement date."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _yf_suffix(exchange)
    t = yf.Ticker(yf_symbol)

    try:
        eh = t.earnings_history
    except Exception:
        eh = None

    if eh is None or (hasattr(eh, "empty") and eh.empty):
        return

    rows = []
    for date, row in eh.iterrows():
        report_ts = _to_utc(date)
        rows.append({
            "event_time": report_ts,
            "knowledge_time": report_ts,
            "ticker": qualified,
            "period": str(row.get("period", "") or ""),
            "eps_estimate": _to_float(row.get("epsEstimate")),
            "eps_actual": _to_float(row.get("epsActual")),
            "surprise_pct": _to_float(row.get("surprisePercent")),
        })
        _write_provenance(db, qualified, report_ts, report_ts,
                          table_name="earnings_history",
                          source_name="yfinance",
                          source_url=f"https://finance.yahoo.com/quote/{yf_symbol}",
                          source_timestamp=report_ts)

    if rows:
        db._append_df("earnings_history", pd.DataFrame(rows))


def ingest(db: "PitDB", ticker: str, start: str, end: str | None = None):
    """Ingest all data for a ticker, routing fundamentals by exchange."""
    _, exchange = _resolve_ticker(ticker)
    ingest_prices(db, ticker, start, end)
    ingest_corporate_actions(db, ticker, start, end)
    if exchange in _US_EXCHANGES:
        ingest_fundamentals_edgar(db, ticker)
    elif exchange == "SGX":
        ingest_fundamentals_sgx(db, ticker)
    ingest_earnings_history(db, ticker)


def _write_corporate_action(db, ticker, event_ts, knowledge_ts, action_type, factor):
    db._append_df("corporate_actions", pd.DataFrame([{
        "event_time": event_ts,
        "knowledge_time": knowledge_ts,
        "ticker": ticker,
        "action_type": action_type,
        "factor": factor,
    }]))


def _write_provenance(db, ticker, event_ts, knowledge_ts, *,
                      table_name, source_name, source_url, source_timestamp):
    db._append_df("provenance", pd.DataFrame([{
        "event_time": event_ts,
        "knowledge_time": knowledge_ts,
        "ticker": ticker,
        "table_name": table_name,
        "source_name": source_name,
        "source_url": source_url,
        "source_timestamp": source_timestamp,
    }]))


def _get_cik(symbol: str) -> int | None:
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=EDGAR_HEADERS, timeout=15)
    if r.status_code != 200:
        return None
    for entry in r.json().values():
        if entry.get("ticker", "").upper() == symbol.upper():
            return int(entry["cik_str"])
    return None


def _match_announcement_date(
    event_ts: pd.Timestamp,
    announcement_dates: list[pd.Timestamp],
) -> pd.Timestamp | None:
    for dt in announcement_dates:
        if dt >= event_ts:
            return dt
    return None


def _extract_concept(facts: dict, concept: str) -> list[dict]:
    units = facts.get(concept, {}).get("units", {})
    # EPS is denominated in "USD/shares"; monetary values in "USD".
    data = units.get("USD") or units.get("USD/shares") or []
    seen = set()
    out = []
    for item in data:
        if item.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            continue
        key = (item.get("start"), item.get("end"), item.get("filed"))
        if key in seen:
            continue
        seen.add(key)
        if "val" in item and "end" in item and "filed" in item:
            out.append(item)
    return out
