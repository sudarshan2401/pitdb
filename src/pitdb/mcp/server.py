"""
pitdb MCP server — 8 read-only tools for point-in-time financial data.

Start with: pitdb serve [--data-dir PATH]
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pitdb")

_db: Any = None  # set by serve() before the server starts


def _get_db():
    if _db is None:
        raise RuntimeError("pitdb MCP server not initialised — call serve()")
    return _db


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

@mcp.tool()
def get_price(ticker: str, date: str, as_of_date: str) -> dict:
    """
    Return OHLCV for a single trading day, as known at as_of_date.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format (e.g. 'AAPL:NASDAQ').
        date: The trading date to look up (YYYY-MM-DD).
        as_of_date: Knowledge time ceiling — only data known on or before this
                    date is returned. Use today's date for current data.

    Returns:
        Dict with keys: ticker, date, open, high, low, close, volume.
        Empty dict if no data is available.
    """
    db = _get_db()
    hist = db.get_price_history(ticker, date, date, as_of_date)
    if hist.empty:
        return {}
    row = hist.iloc[-1]
    return {
        "ticker": ticker,
        "date": date,
        "open": _safe_float(row.get("open")),
        "high": _safe_float(row.get("high")),
        "low": _safe_float(row.get("low")),
        "close": _safe_float(row.get("close")),
        "volume": int(row.get("volume", 0)) if row.get("volume") is not None else None,
    }


@mcp.tool()
def get_price_history(ticker: str, start: str, end: str, as_of_date: str) -> list[dict]:
    """
    Return OHLCV series for a date range, as known at as_of_date.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format.
        start: Start date inclusive (YYYY-MM-DD).
        end: End date inclusive (YYYY-MM-DD).
        as_of_date: Knowledge time ceiling.

    Returns:
        List of dicts, one per trading day, sorted by date ascending.
    """
    db = _get_db()
    df = db.get_price_history(ticker, start, end, as_of_date)
    if df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        records.append({
            "date": _event_time_str(row["event_time"]),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": int(row["volume"]) if row.get("volume") is not None else None,
        })
    return records


@mcp.tool()
def get_fundamentals(ticker: str, as_of_event: str, as_of_date: str) -> dict:
    """
    Return latest fundamentals (EPS, P/E, revenue) as known at as_of_date.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format.
        as_of_event: Consider only events on or before this date.
        as_of_date: Knowledge time ceiling — SEC filings after this date are excluded.

    Returns:
        Dict with keys: ticker, eps, pe_ratio, revenue. None values mean not available.
    """
    db = _get_db()
    result = db.get_fundamentals(ticker, as_of_event, as_of_date)
    if result is None:
        return {}
    return {
        "ticker": ticker,
        "eps": _safe_float(result.get("eps")),
        "pe_ratio": _safe_float(result.get("pe_ratio")),
        "revenue": _safe_float(result.get("revenue")),
    }


@mcp.tool()
def get_earnings_history(ticker: str, start: str, end: str, as_of_date: str) -> list[dict]:
    """
    Return EPS estimate vs actual series across earnings periods, as known at as_of_date.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format.
        start: Start date of the earnings period range (YYYY-MM-DD).
        end: End date of the earnings period range (YYYY-MM-DD).
        as_of_date: Knowledge time ceiling.

    Returns:
        List of dicts with keys: date, period, eps_estimate, eps_actual, surprise_pct.
    """
    db = _get_db()
    df = db.get_earnings_history(ticker, start, end, as_of_date)
    if df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        records.append({
            "date": _event_time_str(row["event_time"]),
            "period": str(row.get("period", "")),
            "eps_estimate": _safe_float(row.get("eps_estimate")),
            "eps_actual": _safe_float(row.get("eps_actual")),
            "surprise_pct": _safe_float(row.get("surprise_pct")),
        })
    return records


@mcp.tool()
def get_context_pack(ticker: str, as_of_date: str) -> dict:
    """
    Return a complete reasoning bundle for an agent: price snapshot, 30-day return,
    latest fundamentals, and corporate actions in the past 90 days.

    All data is point-in-time correct — only information known at as_of_date is included.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format (e.g. 'AAPL:NASDAQ').
        as_of_date: The date to reason from (YYYY-MM-DD). All data is as-of this date.

    Returns:
        Dict with keys: ticker, as_of_date, price_snapshot, trailing_30d_return,
        fundamentals, corporate_actions_90d.
    """
    db = _get_db()
    return db.get_context_pack(ticker, as_of_date)


@mcp.tool()
def get_corporate_actions(ticker: str, start: str, end: str, as_of_date: str) -> list[dict]:
    """
    Return splits and dividends over a date range, as known at as_of_date.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format.
        start: Start date inclusive (YYYY-MM-DD).
        end: End date inclusive (YYYY-MM-DD).
        as_of_date: Knowledge time ceiling.

    Returns:
        List of dicts with keys: date, action_type ('split' or 'dividend'), factor.
    """
    db = _get_db()
    df = db.get_corporate_actions(ticker, start, end, as_of_date)
    if df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        records.append({
            "date": _event_time_str(row["event_time"]),
            "action_type": str(row.get("action_type", "")),
            "factor": _safe_float(row.get("factor")),
        })
    return records


@mcp.tool()
def check_data_availability(ticker: str, start: str, end: str) -> dict:
    """
    Return record counts per table for a ticker over a date range.

    Useful for knowing whether pitdb has data before running queries.

    Args:
        ticker: Instrument in 'SYM:EXCHANGE' format.
        start: Start date (YYYY-MM-DD).
        end: End date (YYYY-MM-DD).

    Returns:
        Dict with keys: prices, fundamentals, corporate_actions — each an integer count.
    """
    db = _get_db()
    return db.check_data_availability(ticker, start, end)


@mcp.tool()
def list_tickers() -> list[str]:
    """
    Return all distinct tickers in the pitdb prices table.

    Returns:
        List of ticker strings in 'SYM:EXCHANGE' format.
    """
    db = _get_db()
    return db.list_tickers()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _event_time_str(v) -> str:
    return str(v.date()) if hasattr(v, "date") else str(v)


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def serve(db_instance):
    """Start the MCP server bound to `db_instance` (stdio transport)."""
    global _db
    _db = db_instance
    mcp.run()
