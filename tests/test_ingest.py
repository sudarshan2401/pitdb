"""
Integration tests for pitdb ingestion pipelines.

These tests hit live APIs (yfinance, SEC EDGAR) with a minimal data slice.
They verify knowledge_time derivation and provenance record creation.

Mark: pytest -m integration to run separately from unit tests.
"""

import math

import pandas as pd
import pytest

import sys
sys.path.insert(0, "src")

from pitdb import PitDB, ingest_prices, ingest_corporate_actions, ingest_fundamentals_edgar


pytestmark = pytest.mark.integration


@pytest.fixture
def db(tmp_path):
    return PitDB.local(data_dir=str(tmp_path))


class TestIngestPrices:
    def test_writes_rows(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-05")
        prices = db._q("prices").pd()
        assert len(prices) == 3  # Jan 2, 3, 4 (Jan 5 end is exclusive in yfinance)

    def test_knowledge_time_is_market_close(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-05")
        prices = db._q("prices").pd()
        for _, row in prices.iterrows():
            kt = row["knowledge_time"]
            assert kt.hour == 21, f"Expected knowledge_time hour=21 (NYSE close UTC), got {kt.hour}"
            assert kt.minute == 0

    def test_event_time_is_midnight_utc(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-05")
        prices = db._q("prices").pd()
        for _, row in prices.iterrows():
            et = row["event_time"]
            assert et.hour == 0 and et.minute == 0 and et.second == 0

    def test_knowledge_time_same_date_as_event(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-05")
        prices = db._q("prices").pd()
        for _, row in prices.iterrows():
            assert row["knowledge_time"].date() == row["event_time"].date()

    def test_provenance_written(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-05")
        prov = db._q("provenance").pd()
        assert len(prov) >= 3
        assert all(prov["source_name"].astype(str) == "yfinance")
        assert all(prov["table_name"].astype(str) == "prices")

    def test_ohlcv_values_present(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-03")
        prices = db._q("prices").pd()
        row = prices.iloc[0]
        for col in ("open", "high", "low", "close"):
            assert not math.isnan(row[col]), f"{col} should not be NaN"
        assert row["volume"] > 0

    def test_ticker_stored_qualified(self, db):
        ingest_prices(db, "AAPL:NASDAQ", "2024-01-02", "2024-01-03")
        prices = db._q("prices").pd()
        assert all(prices["ticker"].astype(str) == "AAPL:NASDAQ")

    def test_bare_ticker_normalised_to_qualified(self, db):
        ingest_prices(db, "AAPL", "2024-01-02", "2024-01-03")
        prices = db._q("prices").pd()
        assert len(prices) >= 1
        assert all(prices["ticker"].astype(str) == "AAPL:NASDAQ")


class TestIngestCorporateActions:
    def test_aapl_splits_present(self, db):
        ingest_corporate_actions(db, "AAPL:NASDAQ", "2010-01-01")
        ca = db._q("corporate_actions").pd()
        splits = ca[ca["action_type"].astype(str) == "split"]
        # AAPL had splits in 2014 and 2020 within this range
        assert len(splits) >= 2

    def test_2020_split_factor(self, db):
        # yfinance .splits returns full history regardless of start/end
        ingest_corporate_actions(db, "AAPL:NASDAQ", "2020-01-01")
        ca = db._q("corporate_actions").pd()
        splits = ca[ca["action_type"].astype(str) == "split"]
        aug_2020 = splits[splits["event_time"].dt.year == 2020]
        assert len(aug_2020) == 1
        assert aug_2020.iloc[0]["factor"] == pytest.approx(4.0)

    def test_knowledge_time_equals_event_time(self, db):
        ingest_corporate_actions(db, "AAPL:NASDAQ", "2020-01-01", "2021-01-01")
        ca = db._q("corporate_actions").pd()
        for _, row in ca.iterrows():
            assert row["knowledge_time"] == row["event_time"]

    def test_dividends_present(self, db):
        ingest_corporate_actions(db, "AAPL:NASDAQ", "2024-01-01")
        ca = db._q("corporate_actions").pd()
        dividends = ca[ca["action_type"].astype(str) == "dividend"]
        assert len(dividends) >= 1


class TestIngestFundamentalsEdgar:
    def test_writes_rows(self, db):
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        assert len(fund) > 0

    def test_knowledge_time_never_before_event_time(self, db):
        """Filing date must always be on or after the period end date."""
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        violations = fund[fund["knowledge_time"] < fund["event_time"]]
        assert len(violations) == 0, (
            f"knowledge_time before event_time in {len(violations)} rows:\n{violations}"
        )

    def test_eps_values_present(self, db):
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        eps_rows = fund[fund["eps"].notna()]
        assert len(eps_rows) > 0

    def test_revenue_values_present(self, db):
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        rev_rows = fund[fund["revenue"].notna()]
        assert len(rev_rows) > 0

    def test_provenance_source_is_edgar(self, db):
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        prov = db._q("provenance").pd()
        edgar_prov = prov[prov["source_name"].astype(str) == "SEC EDGAR"]
        assert len(edgar_prov) > 0

    def test_knowledge_time_matches_provenance(self, db):
        """Provenance source_timestamp should equal the row's knowledge_time."""
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        prov = db._q("provenance").pd()
        prov_fund = prov[prov["table_name"].astype(str) == "fundamentals"]
        for _, row in prov_fund.iterrows():
            assert row["source_timestamp"] == row["knowledge_time"], (
                f"source_timestamp {row['source_timestamp']} != knowledge_time {row['knowledge_time']}"
            )

    def test_point_in_time_fundamentals_query(self, db):
        """After ingestion, a PIT query before a known filing date returns None."""
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")
        fund = db._q("fundamentals").pd()
        eps_rows = fund[fund["eps"].notna()].sort_values("knowledge_time")

        # Pick a row and query just before its knowledge_time — should return nothing
        row = eps_rows.iloc[-1]
        one_day_before = row["knowledge_time"] - pd.Timedelta(days=1)

        result = db.get_fundamentals(
            "AAPL:NASDAQ",
            as_of_event=row["event_time"],
            as_of_knowledge=one_day_before,
        )
        # Either None (if this was the earliest record) or eps differs from this row's value
        if result is not None:
            assert result["eps"] != pytest.approx(row["eps"])
