"""
Snapshot — a lazy, read-only view scoped to a knowledge_time ceiling.

All query methods are the same as PitDB but with as_of_knowledge
pre-filled from the snapshot's bound time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd


class Snapshot:
    def __init__(self, db: "PitDB", as_of_knowledge: str | datetime):
        self._db = db
        self._kt = pd.Timestamp(as_of_knowledge, tz="UTC") if isinstance(as_of_knowledge, str) else as_of_knowledge

    @property
    def knowledge_time(self) -> pd.Timestamp:
        return self._kt

    def get_price_history(self, ticker: str, start, end) -> pd.DataFrame:
        return self._db.get_price_history(ticker, start, end, self._kt)

    def get_fundamentals(self, ticker: str, as_of_event) -> pd.Series | None:
        return self._db.get_fundamentals(ticker, as_of_event, self._kt)

    def get_earnings_history(self, ticker: str, start, end) -> pd.DataFrame:
        return self._db.get_earnings_history(ticker, start, end, self._kt)

    def get_corporate_actions(self, ticker: str, start, end) -> pd.DataFrame:
        return self._db.get_corporate_actions(ticker, start, end, self._kt)

    def get_context_pack(self, ticker: str) -> dict:
        return self._db.get_context_pack(ticker, self._kt)

    def __repr__(self):
        return f"Snapshot(knowledge_time={self._kt.isoformat()})"
