from __future__ import annotations

import os
import ipaddress
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pykx as kx

from .schema import SCHEMA_Q

_FUNCTIONS_Q_PATH = Path(__file__).parent / "functions.q"
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


class PitDB:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        allow_insecure_remote: bool = False,
        allow_unsafe_query: bool | None = None,
    ):
        if not allow_insecure_remote and not self._is_loopback_host(host):
            raise ValueError(
                f"Refusing insecure remote connection to '{host}'. "
                "Use localhost/loopback or set allow_insecure_remote=True explicitly."
            )
        self._mode = "ipc"
        self._allow_unsafe_query = self._unsafe_query_enabled(allow_unsafe_query)
        self._conn = kx.QConnection(host=host, port=port)
        self._conn(SCHEMA_Q)
        self._conn(f"\\l {_FUNCTIONS_Q_PATH}")

    @classmethod
    def local(cls, data_dir: str | None = None) -> "PitDB":
        instance = cls.__new__(cls)
        instance._mode = "embedded"
        instance._conn = None
        instance._allow_unsafe_query = cls._unsafe_query_enabled(None)
        instance._data_dir = Path(data_dir or os.path.expanduser("~/.pitdb/data"))
        instance._data_dir.mkdir(parents=True, exist_ok=True)
        kx.q(SCHEMA_Q)
        kx.q(f"\\l {_FUNCTIONS_Q_PATH}")
        instance._load_tables()
        return instance

    @staticmethod
    def _unsafe_query_enabled(explicit: bool | None) -> bool:
        if explicit is not None:
            return explicit
        return os.getenv("PITDB_ENABLE_UNSAFE_QUERY", "").strip().lower() in _TRUE_ENV_VALUES

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host == "localhost":
            return True
        # Accept IPv6 literals that may be passed in bracket form (e.g. "[::1]").
        normalized = host.strip().strip("[]")
        try:
            return ipaddress.ip_address(normalized).is_loopback
        except ValueError:
            pass
        try:
            resolved = socket.getaddrinfo(normalized, None)
        except socket.gaierror:
            return False
        ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for entry in resolved:
            try:
                ips.append(ipaddress.ip_address(entry[4][0].strip("[]")))
            except ValueError:
                return False
        return bool(ips) and all(ip.is_loopback for ip in ips)

    def _q(self, code: str, *args) -> Any:
        if self._mode == "embedded":
            return kx.q(code, *args)
        return self._conn(code, *args)

    def _sym(self, name: str) -> Any:
        return kx.toq(name, ktype=kx.SymbolAtom)

    def _to_kx_ts(self, dt) -> Any:
        ts = pd.Timestamp(dt)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        # PyKX beta requires timezone-naive UTC as a plain Python datetime.
        naive_utc = ts.tz_convert("UTC").tz_localize(None).to_pydatetime()
        return kx.toq(naive_utc, ktype=kx.TimestampAtom)

    def _append_df(self, table: str, df: pd.DataFrame):
        self._q("{[tbl;t] tbl insert t}", self._sym(table), kx.toq(df))

    def _load_tables(self):
        for name in ("prices", "fundamentals", "earnings_history",
                     "corporate_actions", "provenance"):
            path = self._data_dir / name
            if path.exists():
                self._q("{[n;p] pitLoadTable[n;p]}", kx.toq(name, ktype=kx.SymbolAtom), str(path))

    def _save_tables(self):
        for name in ("prices", "fundamentals", "earnings_history",
                     "corporate_actions", "provenance"):
            path = self._data_dir / name
            self._q("{[p;t] pitSaveTable[p;t]}", str(path), self._q(name))

    def get_as_of(
        self,
        ticker: str,
        field: str,
        as_of_event: str | datetime,
        as_of_knowledge: str | datetime,
        table: str = "prices",
    ) -> Any:
        et = self._to_kx_ts(as_of_event)
        kt = self._to_kx_ts(as_of_knowledge)
        tkr = self._sym(ticker)
        result = self._q(
            "{[tbl;tkr;et;kt] pitGet[tbl;tkr;et;kt]}",
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
        tkr = self._sym(ticker)
        result = self._q(
            "{[tkr;s;e;kt] pitPriceHistory[tkr;s;e;kt]}",
            tkr, self._to_kx_ts(start), self._to_kx_ts(end), self._to_kx_ts(as_of_date),
        )
        return result.pd().reset_index()

    def get_fundamentals(
        self,
        ticker: str,
        as_of_event: str | datetime,
        as_of_knowledge: str | datetime,
    ) -> pd.Series | None:
        """pe_ratio is computed at query time as latest known close / EPS."""
        tkr = self._sym(ticker)
        et = self._to_kx_ts(as_of_event)
        kt = self._to_kx_ts(as_of_knowledge)
        df = self._q("{[tkr;et;kt] pitFundamentals[tkr;et;kt]}", tkr, et, kt).pd()
        if df.empty:
            return None
        row = df.iloc[-1].copy()

        eps = row.get("eps")
        pe_ratio = float("nan")
        if eps is not None and not (isinstance(eps, float) and pd.isna(eps)) and float(eps) != 0:
            price_result = self._q("{[tkr;et;kt] pitLatestClose[tkr;et;kt]}", tkr, et, kt)
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
        tkr = self._sym(ticker)
        result = self._q(
            "{[tkr;s;e;kt] pitEarningsHistory[tkr;s;e;kt]}",
            tkr, self._to_kx_ts(start), self._to_kx_ts(end), self._to_kx_ts(as_of_date),
        )
        return result.pd().reset_index()

    def get_corporate_actions(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
        as_of_date: str | datetime,
    ) -> pd.DataFrame:
        tkr = self._sym(ticker)
        result = self._q(
            "{[tkr;s;e;kt] pitCorpActions[tkr;s;e;kt]}",
            tkr, self._to_kx_ts(start), self._to_kx_ts(end), self._to_kx_ts(as_of_date),
        )
        return result.pd()

    def get_context_pack(self, ticker: str, as_of_date: str | datetime) -> dict:
        """Returns: price_snapshot, trailing_30d_return, fundamentals, corporate_actions_90d."""
        as_of = pd.Timestamp(as_of_date, tz="UTC") if isinstance(as_of_date, str) else as_of_date
        start_30d = as_of - timedelta(days=30)
        start_90d = as_of - timedelta(days=90)

        prices = self.get_price_history(ticker, start_30d, as_of, as_of)
        fundamentals = self.get_fundamentals(ticker, as_of, as_of)
        actions = self.get_corporate_actions(ticker, start_90d, as_of, as_of)

        price_snapshot = prices.iloc[-1].to_dict() if not prices.empty else {}
        trailing_return = None
        if len(prices) >= 2:
            trailing_return = prices["close"].iloc[-1] / prices["close"].iloc[0] - 1

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
        from .snapshot import Snapshot
        return Snapshot(self, as_of_knowledge)

    def walk(
        self,
        start: str | datetime,
        end: str | datetime,
        knowledge_lag: timedelta = timedelta(0),
    ):
        """Yields (date, Snapshot) pairs. Each Snapshot's knowledge_time = date + knowledge_lag."""
        from .snapshot import Snapshot
        for dt in pd.bdate_range(start=start, end=end, freq="B"):
            yield dt, Snapshot(self, pd.Timestamp(dt, tz="UTC") + knowledge_lag)

    def provenance(self, ticker: str, event_time: str | datetime | None = None) -> pd.DataFrame:
        """Full audit trail for ticker. Optionally filter to a specific period-end date."""
        tkr = self._sym(ticker)
        if event_time is not None:
            result = self._q("{[tkr;et] pitProvenanceAt[tkr;et]}", tkr, self._to_kx_ts(event_time))
        else:
            result = self._q("{[tkr] pitProvenance[tkr]}", tkr)
        return result.pd()

    def check_data_availability(
        self,
        ticker: str,
        start: str | datetime,
        end: str | datetime,
    ) -> dict:
        return self._q(
            "{[tkr;s;e] pitAvailability[tkr;s;e]}",
            self._sym(ticker), self._to_kx_ts(start), self._to_kx_ts(end),
        ).py()

    def list_tickers(self) -> list[str]:
        """Returns tickers from the prices table."""
        return [str(t) for t in self._q("exec distinct ticker from prices").py()]

    def query(self, q_string: str) -> Any:
        """Escape hatch for raw q."""
        if not self._allow_unsafe_query:
            raise RuntimeError(
                "db.query() is disabled by default for safety. "
                "Set PITDB_ENABLE_UNSAFE_QUERY=1 or construct PitDB with "
                "allow_unsafe_query=True to enable it."
            )
        return self._q(q_string)

    def save(self):
        """Persist tables to disk. Local mode only."""
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
