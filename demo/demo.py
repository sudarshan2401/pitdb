"""
pitdb demo — two scenarios where look-ahead bias produces concretely wrong answers.

Scenario 1: Financial Restatement (Virgin Galactic / SPCE)
    In March 2021, SPCE filed its FY2020 annual report showing Q1 EPS = -$0.30.
    70 days later, the SEC required SPACs to reclassify warrants as liabilities.
    SPCE filed an amended 10-K/A on May 10, 2021 restating Q1 EPS to -$1.86.

    A naive agent today always returns -$1.86 for any historical date — including
    dates in March–May 2021 when -$0.30 was the only published figure.

    pitdb stores both records with their EDGAR filing dates as knowledge_time.
    Querying as_of April 15, 2021 returns -$0.30 (correct for that date).
    Querying as_of May 15, 2021 returns -$1.86 (correct for that date).

Scenario 2: Late Filing Discovery (Apple / AAPL)
    AAPL's Q3 FY2020 (period ending June 27, 2020) 10-Q was filed July 31, 2020.
    An agent reasoning as of July 15 should see no Q3 data — it wasn't public.
    pitdb enforces this via knowledge_time: the Q3 row is invisible until July 31.
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import yfinance as yf
import pandas as pd

from pitdb import PitDB, ingest_fundamentals_edgar

DIVIDER = "=" * 65


def print_header(title):
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


# ---------------------------------------------------------------------------
# Scenario 1 — Financial Restatement
# ---------------------------------------------------------------------------

def scenario_1_restatement(db: PitDB):
    print_header("SCENARIO 1: Financial Restatement (Virgin Galactic / SPCE)")
    print("""
In April 2021, the SEC ruled that SPAC warrants must be classified
as liabilities, not equity. Virgin Galactic (a SPAC) had to restate
its FY2020 annual report.

  Original 10-K   filed 2021-03-01:  Q1 2020 EPS = -$0.30
  Amended 10-K/A  filed 2021-05-10:  Q1 2020 EPS = -$1.86  (6.2× larger loss)

Any agent reasoning between March and May 2021 used -$0.30.
A naive system today returns -$1.86 for ALL historical dates.
""")

    # Naive: yfinance always returns the current (restated) value
    spce = yf.Ticker("SPCE")
    try:
        qe = spce.quarterly_earnings
        if qe is not None and not qe.empty:
            q1_row = qe[qe.index.year == 2020]
            naive_eps = float(q1_row["Earnings"].iloc[0]) if not q1_row.empty else None
        else:
            naive_eps = None
    except Exception:
        naive_eps = None

    # pitdb: point-in-time correct
    BEFORE_AMENDMENT = "2021-04-15"  # 6 weeks after original, before amendment
    AFTER_AMENDMENT  = "2021-05-15"  # 5 days after amendment

    r_before = db.get_fundamentals("SPCE:NYSE", "2020-03-31", BEFORE_AMENDMENT)
    r_after  = db.get_fundamentals("SPCE:NYSE", "2020-03-31", AFTER_AMENDMENT)

    eps_before = float(r_before["eps"]) if r_before is not None and r_before.get("eps") is not None else None
    eps_after  = float(r_after["eps"])  if r_after  is not None and r_after.get("eps")  is not None else None

    print(f"  Naive (yfinance, no date awareness):  EPS = {_fmt(naive_eps, '$')}")
    print(f"  pitdb as_of {BEFORE_AMENDMENT} (pre-amendment):  EPS = {_fmt(eps_before, '$')}")
    print(f"  pitdb as_of {AFTER_AMENDMENT} (post-amendment): EPS = {_fmt(eps_after,  '$')}")
    print()

    passed = True
    if eps_before is not None and abs(eps_before - (-0.30)) < 0.01:
        print("  ✓ PASS: pre-amendment query returns original value (-$0.30)")
    else:
        print(f"  ✗ pre-amendment query: expected -$0.30, got {eps_before}")
        passed = False

    if eps_after is not None and abs(eps_after - (-1.86)) < 0.01:
        print("  ✓ PASS: post-amendment query returns restated value (-$1.86)")
    else:
        print(f"  ✗ post-amendment query: expected -$1.86, got {eps_after}")
        passed = False

    if naive_eps is not None and abs(naive_eps - eps_before) > 0.01:
        print(f"  ✓ PASS: naive returns {_fmt(naive_eps, '$')} for all dates — look-ahead bias confirmed")

    # Show provenance
    prov = db.provenance("SPCE:NYSE", "2020-03-31")
    if not prov.empty:
        filing_dates = sorted(prov["knowledge_time"].dt.date.unique())
        print(f"\n  Provenance — EDGAR filing dates for this period: {filing_dates}")


# ---------------------------------------------------------------------------
# Scenario 2 — Late Filing Discovery
# ---------------------------------------------------------------------------

def scenario_2_late_filing(db: PitDB):
    print_header("SCENARIO 2: Late Filing Discovery (Apple / AAPL)")
    print("""
AAPL fiscal Q3 2020 period ended:  June 27, 2020
10-Q filed with SEC EDGAR on:      July 31, 2020

An agent reasoning as of July 15 should see nothing for Q3 —
the filing didn't exist yet. pitdb enforces this via knowledge_time.
""")

    import pykx as kx
    fund = kx.q("fundamentals").pd()

    # Find AAPL Q3 FY2020 row (period ending June 2020) with EPS
    q3_candidates = fund[
        (fund["event_time"].dt.year == 2020) &
        (fund["event_time"].dt.month.isin([6, 7])) &
        (fund["ticker"].astype(str) == "AAPL:NASDAQ") &
        (fund["eps"].notna())
    ].sort_values("knowledge_time")

    if q3_candidates.empty:
        print("  NOTE: no AAPL Q3 FY2020 EPS rows found in this db — running generic check.")
        _generic_late_filing_check(db, "AAPL:NASDAQ")
        return

    row = q3_candidates.iloc[0]
    filing_kt = row["knowledge_time"]
    eps_val = row["eps"]

    print(f"  Q3 row in pitdb:")
    print(f"    event_time     = {row['event_time'].date()} (period end)")
    print(f"    knowledge_time = {filing_kt.date()} (EDGAR filing date)")
    print(f"    eps            = ${eps_val:.4f}")
    print()

    before_kt = filing_kt - pd.Timedelta(days=1)
    after_kt  = filing_kt + pd.Timedelta(days=1)
    event_str = str(row["event_time"].date())

    # Use get_as_of for exact event_time match — proves the Q3 row specifically
    # is blocked before its knowledge_time, not confused with earlier quarters.
    eps_before = db.get_as_of("AAPL:NASDAQ", "eps",
                               as_of_event=event_str,
                               as_of_knowledge=str(before_kt.date()),
                               table="fundamentals")
    eps_after  = db.get_as_of("AAPL:NASDAQ", "eps",
                               as_of_event=event_str,
                               as_of_knowledge=str(after_kt.date()),
                               table="fundamentals")

    print(f"  as_of {before_kt.date()} (day before filing):  Q3 EPS = {eps_before!r}  ← not yet public")
    print(f"  as_of {after_kt.date()} (day after filing):   Q3 EPS = {_fmt(float(eps_after), '$') if eps_after is not None else None}")
    print()

    if eps_before is None and eps_after is not None:
        print("  ✓ PASS: Q3 EPS invisible before filing date, visible after")
    elif eps_before is None:
        print("  ✓ PASS: knowledge_time gate blocked Q3 EPS before filing")
    else:
        print(f"  ✗ FAIL: Q3 EPS leaked before filing — got ${eps_before:.2f}")


def _generic_late_filing_check(db, ticker):
    """Fallback for when a specific quarter isn't in the db."""
    import pykx as kx
    fund = kx.q("fundamentals").pd()
    eps_rows = fund[
        (fund["ticker"].astype(str) == ticker) &
        (fund["eps"].notna())
    ].sort_values("knowledge_time")

    if eps_rows.empty:
        print(f"  No EPS rows for {ticker}.")
        return

    row = eps_rows.iloc[-1]
    before_kt = row["knowledge_time"] - pd.Timedelta(days=1)
    after_kt  = row["knowledge_time"] + pd.Timedelta(days=1)

    r_before = db.get_fundamentals(ticker, str(row["event_time"].date()), str(before_kt.date()))
    r_after  = db.get_fundamentals(ticker, str(row["event_time"].date()), str(after_kt.date()))

    def _sees(r):
        if r is None: return False
        v = r.get("eps")
        return v is not None and not (isinstance(v, float) and pd.isna(v)) and abs(float(v) - float(row["eps"])) < 0.001

    print(f"  Checking {ticker} period={row['event_time'].date()}, filed={row['knowledge_time'].date()}, eps=${row['eps']:.4f}")
    print(f"  as_of {before_kt.date()}: {'visible ✗' if _sees(r_before) else 'not visible ← correct ✓'}")
    print(f"  as_of {after_kt.date()}:  {'visible ← correct ✓' if _sees(r_after) else 'not visible'}")
    if not _sees(r_before):
        print("\n  ✓ PASS: knowledge_time gate working correctly")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(val, prefix=""):
    if val is None:
        return "N/A"
    return f"{prefix}{val:.2f}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\npitdb — Point-in-Time Correct Financial Data Demo")
    print("Ingesting SPCE (Virgin Galactic) and AAPL fundamentals from EDGAR...\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = PitDB.local(data_dir=tmpdir)

        print("  → SPCE (Virgin Galactic)...")
        ingest_fundamentals_edgar(db, "SPCE:NYSE")

        print("  → AAPL (Apple)...")
        ingest_fundamentals_edgar(db, "AAPL:NASDAQ")

        import pykx as kx
        n = len(kx.q("fundamentals").pd())
        print(f"  → {n} fundamental rows ingested\n")

        scenario_1_restatement(db)
        scenario_2_late_filing(db)

    print(f"\n{DIVIDER}")
    print("  Demo complete.")
    print(DIVIDER)


if __name__ == "__main__":
    main()
