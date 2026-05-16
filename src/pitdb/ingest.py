"""
Ingestion pipelines for pitdb.

knowledge_time is derived from source metadata, not ingest time:
  - Prices: market close time on event_date (data is public at EOD)
  - US fundamentals: SEC EDGAR filing date (10-K/Q including amendments)
  - SGX fundamentals: SGX announcements API date (dual provenance per ADR 0003)
  - Corporate actions: announcement date from yfinance
  - Earnings history: earnings announcement date from yfinance

All writes are append-only via db._append_df().
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd
import requests
import yfinance as yf

if TYPE_CHECKING:
    from .connection import PitDB

EDGAR_HEADERS = {"User-Agent": "pitdb/0.1 sudarshan.k@u.nus.edu"}

# yfinance symbol suffixes by exchange identifier
_YF_SUFFIX: dict[str, str] = {
    "NYSE": "", "NASDAQ": "", "AMEX": "", "OTC": "",
    "TSX": ".TO", "TSXV": ".V",
    "LSE": ".L",
    "SGX": ".SI",
    "ASX": ".AX",
    "HKEX": ".HK",
    "TSE": ".T",
    "NSE": ".NS", "BSE": ".BO",
    "SSE": ".SS", "SZSE": ".SZ",
    "KRX": ".KS",
    "EPA": ".PA",
    "AMS": ".AS",
    "FRA": ".F",
    "XETRA": ".DE",
    "BME": ".MC",
    "SIX": ".SW",
    "TWSE": ".TW",
    "SET": ".BK",
    "IDX": ".JK",
    "BOVESPA": ".SA",
    "BCBA": ".BA",
    "BMV": ".MX",
    "CPH": ".CO",
}

# Market close time in UTC hours by exchange (approximate; ignores DST for date-level accuracy)
_CLOSE_UTC_HOUR: dict[str, int] = {
    "NYSE": 21, "NASDAQ": 21, "AMEX": 21, "OTC": 21,
    "TSX": 21, "TSXV": 21,
    "LSE": 16,
    "SGX": 9,
    "ASX": 6,
    "HKEX": 8,
    "TSE": 6,
    "NSE": 10, "BSE": 10,
    "SSE": 7, "SZSE": 7,
    "KRX": 6,
    "EPA": 16, "AMS": 16,
    "FRA": 16, "XETRA": 16,
    "BME": 16,
    "SIX": 16,
    "TWSE": 6,
    "SET": 10,
    "IDX": 9,
}

_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "OTC"}


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _resolve_ticker(ticker: str) -> tuple[str, str]:
    """Split 'AAPL:NASDAQ' → ('AAPL', 'NASDAQ'). Bare 'AAPL' → ('AAPL', 'NASDAQ')."""
    if ":" in ticker:
        sym, exchange = ticker.split(":", 1)
        return sym, exchange
    return ticker, "NASDAQ"


def _to_utc(ts) -> pd.Timestamp:
    """Convert any timestamp to UTC, whether tz-aware or tz-naive."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def _yf_sym(ticker: str) -> str:
    """Return the yfinance-ready symbol with exchange suffix (e.g. 'D05.SI')."""
    sym, exchange = _resolve_ticker(ticker)
    return sym + _YF_SUFFIX.get(exchange, "")


# ------------------------------------------------------------------
# Public ingestion functions
# ------------------------------------------------------------------

def ingest_prices(db: "PitDB", ticker: str, start: str, end: str | None = None):
    """Fetch daily OHLCV from yfinance. knowledge_time = market close on each date."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _YF_SUFFIX.get(exchange, "")
    close_hour = _CLOSE_UTC_HOUR.get(exchange, 21)

    raw = yf.download(yf_symbol, start=start, end=end, auto_adjust=False, progress=False)
    if raw.empty:
        return

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    rows = []
    for date, row in raw.iterrows():
        date_ts = _to_utc(date).replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
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
    """Fetch splits and dividends from yfinance. knowledge_time = announcement date."""
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _YF_SUFFIX.get(exchange, "")
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
    """
    Fetch fundamentals from SEC EDGAR XBRL API (US equities only).
    knowledge_time = EDGAR filing date (when the filing became public).
    """
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
    """
    Fetch SGX fundamentals from yfinance.
    knowledge_time = earnings announcement date from yfinance earnings_dates.
    event_time = period-end date from quarterly_income_stmt.
    Single provenance source (yfinance provides both dates and values).
    Note: announcement dates are approximate — yfinance timestamps reflect when
    data became available in Yahoo's system, not the official SGX filing time.
    """
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _YF_SUFFIX.get(exchange, ".SI")
    t = yf.Ticker(yf_symbol)
    yf_url = f"https://finance.yahoo.com/quote/{yf_symbol}"

    def _val(v):
        try:
            f = float(v)
            return f if f == f else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    # Build announcement date list from earnings_dates (most reliable yfinance source)
    try:
        ed = t.earnings_dates
    except Exception:
        ed = None

    announcement_dates: list[pd.Timestamp] = []
    eps_by_announcement: dict[pd.Timestamp, float] = {}
    if ed is not None and not ed.empty:
        for ann_ts, row in ed.iterrows():
            ann_day = _to_utc(ann_ts).replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
            announcement_dates.append(ann_day)
            reported = row.get("Reported EPS")
            if reported is not None and reported == reported:
                eps_by_announcement[ann_day] = float(reported)
        announcement_dates = sorted(announcement_dates)

    # Get quarterly income statement for period-end dates and values
    try:
        qi = t.quarterly_income_stmt
    except Exception:
        qi = None

    if qi is None or qi.empty:
        return

    rows = []
    for period_end in qi.columns:
        event_ts = _to_utc(period_end).replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
        knowledge_ts = _match_announcement_date(event_ts, announcement_dates) or event_ts

        # EPS: prefer income_stmt Basic EPS, fall back to earnings_dates Reported EPS
        eps = float("nan")
        if "Basic EPS" in qi.index:
            eps = _val(qi.loc["Basic EPS", period_end])
        if (eps != eps) and knowledge_ts in eps_by_announcement:
            eps = eps_by_announcement[knowledge_ts]

        revenue = float("nan")
        for label in ("Total Revenue", "Revenue"):
            if label in qi.index:
                revenue = _val(qi.loc[label, period_end])
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
    """
    Fetch earnings history (EPS estimate vs actual) from yfinance.
    knowledge_time = earnings announcement date (when results became public).
    """
    sym, exchange = _resolve_ticker(ticker)
    qualified = f"{sym}:{exchange}"
    yf_symbol = sym + _YF_SUFFIX.get(exchange, "")
    t = yf.Ticker(yf_symbol)

    try:
        eh = t.earnings_history
    except Exception:
        eh = None

    if eh is None or (hasattr(eh, "empty") and eh.empty):
        return

    def _val(v):
        return float(v) if v is not None and v == v else float("nan")

    rows = []
    for date, row in eh.iterrows():
        report_ts = _to_utc(date)
        rows.append({
            "event_time": report_ts,
            "knowledge_time": report_ts,
            "ticker": qualified,
            "period": str(row.get("period", "") or ""),
            "eps_estimate": _val(row.get("epsEstimate")),
            "eps_actual": _val(row.get("epsActual")),
            "surprise_pct": _val(row.get("surprisePercent")),
        })
        _write_provenance(db, qualified, report_ts, report_ts,
                          table_name="earnings_history",
                          source_name="yfinance",
                          source_url=f"https://finance.yahoo.com/quote/{yf_symbol}",
                          source_timestamp=report_ts)

    if rows:
        db._append_df("earnings_history", pd.DataFrame(rows))


def ingest(db: "PitDB", ticker: str, start: str, end: str | None = None):
    """Ingest all data for a ticker. Routes fundamentals to the correct source by exchange."""
    _, exchange = _resolve_ticker(ticker)
    ingest_prices(db, ticker, start, end)
    ingest_corporate_actions(db, ticker, start, end)
    if exchange in _US_EXCHANGES:
        ingest_fundamentals_edgar(db, ticker)
    elif exchange == "SGX":
        ingest_fundamentals_sgx(db, ticker)
    ingest_earnings_history(db, ticker)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_corporate_action(db, ticker, event_ts, knowledge_ts, action_type, factor):
    df = pd.DataFrame([{
        "event_time": event_ts,
        "knowledge_time": knowledge_ts,
        "ticker": ticker,
        "action_type": action_type,
        "factor": factor,
    }])
    db._append_df("corporate_actions", df)


def _write_provenance(db, ticker, event_ts, knowledge_ts, *,
                      table_name, source_name, source_url, source_timestamp):
    df = pd.DataFrame([{
        "event_time": event_ts,
        "knowledge_time": knowledge_ts,
        "ticker": ticker,
        "table_name": table_name,
        "source_name": source_name,
        "source_url": source_url,
        "source_timestamp": source_timestamp,
    }])
    db._append_df("provenance", df)


def _get_cik(symbol: str) -> int | None:
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(tickers_url, headers=EDGAR_HEADERS, timeout=15)
    if r.status_code != 200:
        return None
    tickers = r.json()
    for entry in tickers.values():
        if entry.get("ticker", "").upper() == symbol.upper():
            return int(entry["cik_str"])
    return None


def _match_announcement_date(
    event_ts: pd.Timestamp,
    announcement_dates: list[pd.Timestamp],
) -> pd.Timestamp | None:
    """Return the earliest announcement date on or after event_ts, or None."""
    for dt in announcement_dates:
        if dt >= event_ts:
            return dt
    return None


def _extract_concept(facts: dict, concept: str) -> list[dict]:
    units = facts.get(concept, {}).get("units", {})
    # EPS uses "USD/shares"; monetary values use "USD"
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
