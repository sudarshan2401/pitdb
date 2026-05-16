"""
PitDB — the primary entry point.

Usage:
    db = PitDB.local()              # managed local kdb-x instance
    db = PitDB("localhost", 5000)   # connect to external kdb-x
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pykx as kx

from .schema import SCHEMA_Q


class PitDB:
    def __init__(self, host: str, port: int):
        self._mode = "ipc"
        self._conn = kx.QConnection(host=host, port=port)
        self._conn(SCHEMA_Q)

    @classmethod
    def local(cls, data_dir: str | None = None) -> PitDB:
        instance = cls.__new__(cls)
        instance._mode = "embedded"
        instance._conn = None
        instance._data_dir = Path(data_dir or os.path.expanduser("~/.pitdb/data"))
        instance._data_dir.mkdir(parents=True, exist_ok=True)
        kx.q(SCHEMA_Q)
        instance._load_tables()
        return instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _q(self, code: str, *args) -> Any:
        if self._mode == "embedded":
            return kx.q(code, *args)
        return self._conn(code, *args)

    def _load_tables(self):
        for name in ("prices", "fundamentals", "earnings_history",
                     "corporate_actions", "provenance"):
            path = self._data_dir / name
            if path.exists():
                self._q("{[n;p] n set get hsym `$string p}", kx.toq(name, ktype=kx.SymbolAtom), str(path))

    def _save_tables(self):
        for name in ("prices", "fundamentals", "earnings_history",
                     "corporate_actions", "provenance"):
            path = self._data_dir / name
            self._q("{[p;t] (hsym `$string p) set t}", str(path), self._q(name))

    def _to_kx_ts(self, dt) -> Any:
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        # PyKX beta requires a plain Python datetime, timezone-naive UTC
        naive_utc = ts.tz_convert("UTC").tz_localize(None).to_pydatetime()
        return kx.toq(naive_utc, ktype=kx.TimestampAtom)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _append_df(self, table: str, df: pd.DataFrame):
        """Append a DataFrame to a kdb-x table. All writes are append-only."""
        kx_table = kx.toq(df)
        self._q("{[tbl;t] tbl insert t}", self._sym(table), kx_table)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _sym(self, ticker: str) -> Any:
        return kx.toq(ticker, ktype=kx.SymbolAtom)

    def get_as_of(
        self,
        ticker: str,
        field: str,
        as_of_event: str | datetime,
        as_of_knowledge: str | datetime,
        table: str = "prices",
    ) -> Any:
        """Return the latest known value of `field` for `ticker` at
        `as_of_event`, as known at `as_of_knowledge`."""
        et = self._to_kx_ts(as_of_event)
        kt = self._to_kx_ts(as_of_knowledge)
        tkr = self._sym(ticker)
        result = self._q(
            "{[tbl;tkr;et;kt] select from (value tbl) where ticker=tkr, event_time=et, knowledge_time<=kt}",
            self._sym(table), tkr, et, kt,
        )
        df = result.pd()
        if df.empty:
            return None
        return df[field].iloc[-1]

    def get_price_history(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
        as_of_date: str | datetime,
    ) -> pd.DataFrame:
        """OHLCV series for `ticker` between `start` and `end`,
        as known at `as_of_date`."""
        tkr = self._sym(ticker)
        start_ts = self._to_kx_ts(start)
        end_ts = self._to_kx_ts(end)
        kt = self._to_kx_ts(as_of_date)
        result = self._q(
            "{[tkr;s;e;kt] select last open, last high, last low, last close, last volume by event_time from prices where ticker=tkr, event_time within (s;e), knowledge_time<=kt}",
            tkr, start_ts, end_ts, kt,
        )
        return result.pd().reset_index()

    def get_fundamentals(
        self,
        ticker: str,
        as_of_event: str | datetime,
        as_of_knowledge: str | datetime,
    ) -> pd.Series | None:
        """Latest fundamentals for `ticker` as known at `as_of_knowledge`,
        for events at or before `as_of_event`. pe_ratio is computed from
        the latest known close price divided by EPS when both are available."""
        tkr = self._sym(ticker)
        et = self._to_kx_ts(as_of_event)
        kt = self._to_kx_ts(as_of_knowledge)
        result = self._q(
            "{[tkr;et;kt] t:select from fundamentals where ticker=tkr,event_time<=et,knowledge_time<=kt; if[0=count t;:()]; e:t`eps;r:t`revenue; ([]ticker:enlist tkr;eps:enlist last e where not null e;revenue:enlist last r where not null r)}",
            tkr, et, kt,
        )
        df = result.pd()
        if df.empty:
            return None
        row = df.iloc[-1].copy()

        # Compute trailing P/E from latest known close price / EPS
        eps = row.get("eps")
        pe_ratio = float("nan")
        if eps is not None and not (isinstance(eps, float) and pd.isna(eps)) and float(eps) != 0:
            price_result = self._q(
                "{[tkr;et;kt] last exec close from (select last close by event_time from prices where ticker=tkr, event_time<=et, knowledge_time<=kt)}",
                tkr, et, kt,
            )
            try:
                price = float(price_result.py())
                if price and not pd.isna(price):
                    pe_ratio = round(price / float(eps), 4)
            except (TypeError, ValueError):
                pass
        row["pe_ratio"] = pe_ratio
        return row

    def get_earnings_history(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
        as_of_date: str | datetime,
    ) -> pd.DataFrame:
        """EPS estimate vs actual series for `ticker`, as known at `as_of_date`."""
        tkr = self._sym(ticker)
        start_ts = self._to_kx_ts(start)
        end_ts = self._to_kx_ts(end)
        kt = self._to_kx_ts(as_of_date)
        result = self._q(
            "{[tkr;s;e;kt] select last eps_estimate, last eps_actual, last surprise_pct by event_time, period from earnings_history where ticker=tkr, event_time within (s;e), knowledge_time<=kt}",
            tkr, start_ts, end_ts, kt,
        )
        return result.pd().reset_index()

    def get_corporate_actions(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
        as_of_date: str | datetime,
    ) -> pd.DataFrame:
        """Splits and dividends for `ticker` over the range, as known at `as_of_date`."""
        tkr = self._sym(ticker)
        start_ts = self._to_kx_ts(start)
        end_ts = self._to_kx_ts(end)
        kt = self._to_kx_ts(as_of_date)
        result = self._q(
            "{[tkr;s;e;kt] select from corporate_actions where ticker=tkr, event_time within (s;e), knowledge_time<=kt}",
            tkr, start_ts, end_ts, kt,
        )
        return result.pd()

    def get_context_pack(
        self,
        ticker: str,
        as_of_date: str | datetime,
    ) -> dict:
        """Fixed-schema JSON bundle for `ticker` at `as_of_date`.
        Contains: OHLCV snapshot, 30-day returns, latest fundamentals,
        corporate actions in past 90 days."""
        as_of = pd.Timestamp(as_of_date, tz="UTC") if isinstance(as_of_date, str) else as_of_date
        start_30d = as_of - timedelta(days=30)
        start_90d = as_of - timedelta(days=90)

        prices = self.get_price_history(ticker, start_30d, as_of, as_of)
        fundamentals = self.get_fundamentals(ticker, as_of, as_of)
        actions = self.get_corporate_actions(ticker, start_90d, as_of, as_of)

        price_snapshot = prices.iloc[-1].to_dict() if not prices.empty else {}
        trailing_return = None
        if len(prices) >= 2:
            trailing_return = (prices["close"].iloc[-1] / prices["close"].iloc[0] - 1)

        def _coerce(v):
            if isinstance(v, (pd.Timestamp, datetime)):
                return str(v)
            try:
                return float(v) if pd.notna(v) else None
            except (TypeError, ValueError):
                return str(v) if v is not None else None

        return {
            "ticker": ticker,
            "as_of_date": str(as_of.date()),
            "price_snapshot": {k: _coerce(v) for k, v in price_snapshot.items()},
            "trailing_30d_return": round(float(trailing_return), 6) if trailing_return is not None else None,
            "fundamentals": fundamentals.to_dict() if fundamentals is not None else {},
            "corporate_actions_90d": actions.to_dict(orient="records"),
        }

    def snapshot(self, as_of_knowledge: str | datetime) -> "Snapshot":
        """Return a lazy Snapshot bound to `as_of_knowledge`."""
        from .snapshot import Snapshot
        return Snapshot(self, as_of_knowledge)

    def walk(
        self,
        start: str | datetime,
        end: str | datetime,
        knowledge_lag: timedelta = timedelta(0),
    ):
        """Yield (date, Snapshot) pairs advancing through market dates.
        Each Snapshot is bound to knowledge_time = event_time + knowledge_lag."""
        from .snapshot import Snapshot
        dates = pd.bdate_range(start=start, end=end, freq="B")

        for dt in dates:
            kt = pd.Timestamp(dt, tz="UTC") + knowledge_lag
            yield dt, Snapshot(self, kt)

    def provenance(self, ticker: str, event_time: str | datetime | None = None) -> pd.DataFrame:
        """Return provenance records for `ticker`.
        If `event_time` is given, filter to that exact period-end date.
        Omit it to get the full audit trail for the ticker across all tables."""
        tkr = self._sym(ticker)
        if event_time is not None:
            et = self._to_kx_ts(event_time)
            result = self._q(
                "{[tkr;et] select from provenance where ticker=tkr, event_time=et}",
                tkr, et,
            )
        else:
            result = self._q(
                "{[tkr] select from provenance where ticker=tkr}",
                tkr,
            )
        return result.pd()

    def check_data_availability(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
    ) -> dict:
        """Return record counts per table for `ticker` over the range."""
        tkr = self._sym(ticker)
        s = self._to_kx_ts(start)
        e = self._to_kx_ts(end)
        result = self._q(
            "{[tkr;s;e] `prices`fundamentals`corporate_actions!(count select from prices where ticker=tkr, event_time within (s;e); count select from fundamentals where ticker=tkr, event_time within (s;e); count select from corporate_actions where ticker=tkr, event_time within (s;e))}",
            tkr, s, e,
        )
        return result.py()

    def list_tickers(self) -> list[str]:
        """Return all distinct tickers in the prices table."""
        result = self._q("exec distinct ticker from prices")
        return [str(t) for t in result.py()]

    def query(self, q_string: str) -> Any:
        """Escape hatch: execute raw q code and return the result."""
        return self._q(q_string)

    def save(self):
        """Persist all in-memory tables to disk (local mode only)."""
        if self._mode != "embedded":
            raise RuntimeError("save() is only available in local mode")
        self._save_tables()

    def close(self):
        if self._mode == "ipc" and self._conn:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
