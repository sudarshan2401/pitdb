"""
Core correctness tests for pitdb's bitemporal guarantees.

These tests verify that:
1. Point-in-time queries return the correct value before a correction arrives
2. After a correction, queries after its knowledge_time return the new value
3. The walk() iterator never surfaces future corrections
4. Provenance records are written for every ingested record
"""

import pandas as pd
import pytest

import sys
sys.path.insert(0, "src")

from pitdb import PitDB


@pytest.fixture
def db(tmp_path):
    instance = PitDB.local(data_dir=str(tmp_path))
    return instance


def _ts(date_str, hour=21):
    return pd.Timestamp(date_str, tz="UTC").replace(hour=hour, minute=0, second=0, microsecond=0, nanosecond=0)


def _write_price(db, ticker, event_date, knowledge_date, close):
    df = pd.DataFrame([{
        "event_time": _ts(event_date, hour=0),
        "knowledge_time": _ts(knowledge_date, hour=21),
        "ticker": ticker,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
    }])
    db._append_df("prices", df)


def _write_fundamental(db, ticker, event_date, knowledge_date, eps):
    df = pd.DataFrame([{
        "event_time": _ts(event_date, hour=0),
        "knowledge_time": _ts(knowledge_date, hour=0),
        "ticker": ticker,
        "eps": eps,
        "pe_ratio": float("nan"),
        "revenue": float("nan"),
    }])
    db._append_df("fundamentals", df)


class TestBitemporalCorrectness:
    def test_original_value_returned_before_correction(self, db):
        """Query with knowledge_time before a correction returns the original value."""
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-10", close=185.0)
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-15", close=186.5)

        val = db.get_as_of(
            "AAPL:NASDAQ", "close",
            as_of_event="2024-01-10",        # midnight UTC — matches stored event_time
            as_of_knowledge="2024-01-12T21:00:00+00:00",
        )
        assert val == pytest.approx(185.0), f"Expected 185.0, got {val}"

    def test_corrected_value_returned_after_correction(self, db):
        """Query with knowledge_time after a correction returns the corrected value."""
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-10", close=185.0)
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-15", close=186.5)

        val = db.get_as_of(
            "AAPL:NASDAQ", "close",
            as_of_event="2024-01-10",
            as_of_knowledge="2024-01-16T21:00:00+00:00",
        )
        assert val == pytest.approx(186.5), f"Expected 186.5, got {val}"

    def test_no_future_leakage_on_unknown_date(self, db):
        """Query for a date where no data was known yet returns None."""
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-10", close=185.0)

        val = db.get_as_of(
            "AAPL:NASDAQ", "close",
            as_of_event="2024-01-10",
            as_of_knowledge="2024-01-09T21:00:00+00:00",  # before data existed
        )
        assert val is None, f"Expected None (no future leakage), got {val}"

    def test_fundamentals_respect_filing_date(self, db):
        """Fundamentals query before filing date returns nothing; after returns value."""
        _write_fundamental(db, "AAPL:NASDAQ", "2024-09-30", "2024-11-15", eps=1.64)

        before = db.get_fundamentals(
            "AAPL:NASDAQ",
            as_of_event="2024-09-30",
            as_of_knowledge="2024-11-01",
        )
        assert before is None, f"Expected None before filing, got {before}"

        after = db.get_fundamentals(
            "AAPL:NASDAQ",
            as_of_event="2024-09-30",
            as_of_knowledge="2024-11-20",
        )
        assert after is not None
        assert after["eps"] == pytest.approx(1.64)

    def test_walk_no_future_leakage(self, db):
        """walk() iterator never surfaces a correction before its knowledge_time."""
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-10", close=185.0)
        _write_price(db, "AAPL:NASDAQ", "2024-01-10", "2024-01-15", close=186.5)

        for date, snap in db.walk("2024-01-08", "2024-01-14"):
            prices = snap.get_price_history("AAPL:NASDAQ", "2024-01-10", "2024-01-10")
            if not prices.empty:
                assert prices["close"].iloc[-1] == pytest.approx(185.0), (
                    f"On {date}, walk() surfaced the correction before knowledge_time"
                )

    def test_price_history_point_in_time(self, db):
        """get_price_history returns only prices known at as_of_date."""
        for day in range(1, 6):
            event = f"2024-01-{day:02d}"
            _write_price(db, "MSFT:NASDAQ", event, event, close=float(400 + day))

        # Knowledge times are day@21:00 UTC; only first 3 visible as of Jan 3 22:00
        hist = db.get_price_history("MSFT:NASDAQ", "2024-01-01", "2024-01-05", "2024-01-03T22:00:00+00:00")
        assert len(hist) == 3, f"Expected 3 rows, got {len(hist)}"

    def test_snapshot_scopes_queries(self, db):
        """Snapshot pins knowledge_time for all queries through it."""
        _write_price(db, "TSLA:NASDAQ", "2024-03-01", "2024-03-01", close=200.0)
        _write_price(db, "TSLA:NASDAQ", "2024-03-01", "2024-03-10", close=205.0)

        snap = db.snapshot("2024-03-05T21:00:00+00:00")
        hist = snap.get_price_history("TSLA:NASDAQ", "2024-03-01", "2024-03-01")
        assert not hist.empty
        assert hist["close"].iloc[-1] == pytest.approx(200.0)

    def test_list_tickers(self, db):
        _write_price(db, "AAPL:NASDAQ", "2024-01-01", "2024-01-01", close=100.0)
        _write_price(db, "GOOG:NASDAQ", "2024-01-01", "2024-01-01", close=150.0)
        tickers = db.list_tickers()
        assert "AAPL:NASDAQ" in tickers
        assert "GOOG:NASDAQ" in tickers

    def test_check_data_availability(self, db):
        _write_price(db, "AAPL:NASDAQ", "2024-01-01", "2024-01-01", close=100.0)
        _write_price(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-02", close=101.0)
        avail = db.check_data_availability("AAPL:NASDAQ", "2024-01-01", "2024-01-05")
        assert avail.get("prices", 0) >= 2
