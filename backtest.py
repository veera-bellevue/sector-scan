"""
Backtest for the sector rotation scanner's own past calls.

Answers a specific question, honestly: did the classifications this app has
been making actually predict anything? Specifically:

  - Did sectors tagged "Leading" subsequently outperform sectors tagged
    "Lagging" over the following week/month/3 months?
  - Did sectors flagged outperforming_spy=True actually keep outperforming
    SPY going forward, or was that a coincident (not predictive) signal?
  - Did "Defensive rotation" regime calls precede weaker SPY forward returns
    than "Cyclical / risk-on" calls — the pattern the 2000/2007 historical
    read would predict?
  - Did stocks with a higher composite_score actually go on to outperform
    stocks with a lower one?

This is NOT a proper statistical backtest (no significance testing, no
transaction costs, no lookahead-bias auditing beyond the basics below) — it's
a sanity check. Treat "group A beat group B by a wide margin over 50+
observations" as suggestive, and treat anything under BACKTEST_MIN_SAMPLES
as not yet meaningful, full stop.

Run manually:
    python backtest.py

Needs the same SUPABASE_URL / SUPABASE_SERVICE_KEY env vars as scan.py.
"""

import os
import sys
import datetime
import requests
import pandas as pd
import yfinance as yf

from config import BACKTEST_HORIZONS, BACKTEST_MIN_SAMPLES

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# Supabase I/O (same pattern as scan.py, kept standalone so this file can be
# copied out and run independently if needed)
# ---------------------------------------------------------------------------

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb_select(table: str, params: dict) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print(f"  [error] SUPABASE_URL / SUPABASE_SERVICE_KEY not set, cannot read {table}")
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {k: v for k, v in sb_headers().items() if k != "Prefer"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] select from {table} failed: {resp.status_code} {resp.text[:300]}")
        return []
    return resp.json()


def sb_insert(table: str, rows: list[dict]):
    if not SUPABASE_URL or not SUPABASE_KEY or not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = requests.post(url, headers=sb_headers(), json=rows, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] insert into {table} failed: {resp.status_code} {resp.text[:300]}")


# ---------------------------------------------------------------------------
# Pure date/return math — no network, fully unit-testable
# ---------------------------------------------------------------------------

def forward_return(close: pd.Series, from_date, horizon_trading_days: int) -> float | None:
    """
    Given a date-indexed Close price series, find the first trading day on
    or after from_date (the entry), then look horizon_trading_days *trading
    days* forward (not calendar days, since markets are closed weekends/
    holidays) for the exit price. Returns % change, or None if either point
    isn't available (entry before the series starts, or not enough future
    data yet to reach the exit point).
    """
    from_ts = pd.Timestamp(from_date)
    if close.index.tz is not None and from_ts.tz is None:
        from_ts = from_ts.tz_localize(close.index.tz)

    on_or_after = close.index[close.index >= from_ts]
    if len(on_or_after) == 0:
        return None
    entry_date = on_or_after[0]
    entry_pos = close.index.get_loc(entry_date)
    exit_pos = entry_pos + horizon_trading_days
    if exit_pos >= len(close):
        return None  # not enough future data yet — this is expected for recent runs

    entry_price = float(close.iloc[entry_pos])
    exit_price = float(close.iloc[exit_pos])
    if entry_price == 0:
        return None
    return round((exit_price / entry_price - 1) * 100, 2)


def summarize_group(returns: list[float], min_samples: int = BACKTEST_MIN_SAMPLES) -> dict:
    """Mean/count for a group of forward returns, with an explicit
    'not enough data' flag rather than silently reporting a noisy mean."""
    n = len(returns)
    if n < min_samples:
        return {"mean": None, "n": n, "sufficient": False}
    mean = round(sum(returns) / n, 2)
    return {"mean": mean, "n": n, "sufficient": True}


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def fetch_all_runs() -> dict[int, str]:
    """{run_id: run_ts} for every run on record."""
    rows = sb_select("runs", {"select": "id,run_ts"})
    return {r["id"]: r["run_ts"] for r in rows}


def fetch_price_history(ticker: str, start: str, end: str) -> pd.Series | None:
    try:
        df = yf.Ticker(ticker).history(start=start, end=end)
        if df.empty:
            return None
        return df["Close"]
    except Exception as e:
        print(f"  [warn] price fetch failed for {ticker}: {e}")
        return None


def earliest_date(run_map: dict[int, str]) -> str:
    if not run_map:
        return datetime.date.today().isoformat()
    dates = [datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) for ts in run_map.values()]
    return min(dates).date().isoformat()


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------

def backtest_sector_classification(run_map: dict[int, str], price_cache: dict[str, pd.Series]) -> list[dict]:
    """Does 'Leading' actually outperform 'Lagging' going forward?"""
    rows = sb_select("sector_scores", {"select": "run_id,ticker,classification,outperforming_spy"})
    results = []

    by_classification: dict[str, list[float]] = {}
    by_relstrength: dict[str, list[float]] = {}

    for horizon_name, horizon_days in BACKTEST_HORIZONS.items():
        by_classification.clear()
        by_relstrength.clear()

        for row in rows:
            run_ts = run_map.get(row["run_id"])
            ticker = row["ticker"]
            if not run_ts or ticker not in price_cache or price_cache[ticker] is None:
                continue
            from_date = datetime.datetime.fromisoformat(run_ts.replace("Z", "+00:00")).date().isoformat()
            ret = forward_return(price_cache[ticker], from_date, horizon_days)
            if ret is None:
                continue

            by_classification.setdefault(row["classification"], []).append(ret)
            if row.get("outperforming_spy") is not None:
                key = f"outperforming_spy={row['outperforming_spy']}"
                by_relstrength.setdefault(key, []).append(ret)

        for label, returns in by_classification.items():
            s = summarize_group(returns)
            results.append({
                "metric_type": "sector_classification", "group_label": label,
                "horizon": horizon_name, "mean_forward_return_pct": s["mean"],
                "sample_count": s["n"],
                "note": None if s["sufficient"] else f"insufficient data (n={s['n']}, need >= {BACKTEST_MIN_SAMPLES})",
            })
        for label, returns in by_relstrength.items():
            s = summarize_group(returns)
            results.append({
                "metric_type": "sector_relative_strength", "group_label": label,
                "horizon": horizon_name, "mean_forward_return_pct": s["mean"],
                "sample_count": s["n"],
                "note": None if s["sufficient"] else f"insufficient data (n={s['n']}, need >= {BACKTEST_MIN_SAMPLES})",
            })

    return results


def backtest_regime(run_map: dict[int, str], spy_close: pd.Series | None) -> list[dict]:
    """Did 'Defensive rotation' regime calls precede weaker SPY forward
    returns than 'Cyclical / risk-on' calls, as the historical pattern
    (2000, 2007) would predict?"""
    if spy_close is None:
        return []
    rows = sb_select("regime_summary", {"select": "run_id,label"})
    results = []

    for horizon_name, horizon_days in BACKTEST_HORIZONS.items():
        by_label: dict[str, list[float]] = {}
        for row in rows:
            run_ts = run_map.get(row["run_id"])
            if not run_ts:
                continue
            from_date = datetime.datetime.fromisoformat(run_ts.replace("Z", "+00:00")).date().isoformat()
            ret = forward_return(spy_close, from_date, horizon_days)
            if ret is None:
                continue
            by_label.setdefault(row["label"], []).append(ret)

        for label, returns in by_label.items():
            s = summarize_group(returns)
            results.append({
                "metric_type": "regime", "group_label": label,
                "horizon": horizon_name, "mean_forward_return_pct": s["mean"],
                "sample_count": s["n"],
                "note": None if s["sufficient"] else f"insufficient data (n={s['n']}, need >= {BACKTEST_MIN_SAMPLES})",
            })
    return results


def backtest_stock_composite(run_map: dict[int, str], price_cache: dict[str, pd.Series]) -> list[dict]:
    """Did a higher composite_score actually predict better forward
    performance, or is it just a plausible-looking number?"""
    rows = sb_select("stock_scores", {"select": "run_id,ticker,composite_score"})
    if not rows:
        return []

    # Bucket by tercile of composite_score *within each run* (a run's #1
    # stock isn't comparable in absolute score terms across different dates/
    # regimes, but its relative rank within that run's batch is)
    by_run: dict[int, list[dict]] = {}
    for row in rows:
        by_run.setdefault(row["run_id"], []).append(row)

    tercile_rows = []
    for run_id, run_rows in by_run.items():
        if len(run_rows) < 3:
            continue
        sorted_rows = sorted(run_rows, key=lambda r: r["composite_score"], reverse=True)
        n = len(sorted_rows)
        third = max(1, n // 3)
        for i, row in enumerate(sorted_rows):
            tercile = "top_tercile" if i < third else "bottom_tercile" if i >= n - third else "middle_tercile"
            tercile_rows.append({**row, "tercile": tercile})

    results = []
    for horizon_name, horizon_days in BACKTEST_HORIZONS.items():
        by_tercile: dict[str, list[float]] = {}
        for row in tercile_rows:
            run_ts = run_map.get(row["run_id"])
            ticker = row["ticker"]
            if not run_ts or ticker not in price_cache or price_cache[ticker] is None:
                continue
            from_date = datetime.datetime.fromisoformat(run_ts.replace("Z", "+00:00")).date().isoformat()
            ret = forward_return(price_cache[ticker], from_date, horizon_days)
            if ret is None:
                continue
            by_tercile.setdefault(row["tercile"], []).append(ret)

        for label, returns in by_tercile.items():
            s = summarize_group(returns)
            results.append({
                "metric_type": "stock_composite", "group_label": label,
                "horizon": horizon_name, "mean_forward_return_pct": s["mean"],
                "sample_count": s["n"],
                "note": None if s["sufficient"] else f"insufficient data (n={s['n']}, need >= {BACKTEST_MIN_SAMPLES})",
            })
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Backtest run @ {datetime.datetime.utcnow().isoformat()} UTC ===")

    run_map = fetch_all_runs()
    print(f"Found {len(run_map)} historical run(s).")
    if not run_map:
        print("No runs on record yet — nothing to backtest. Let scan.py run a few times first.")
        sys.exit(0)

    start = earliest_date(run_map)
    end = datetime.date.today().isoformat()
    print(f"Pulling price history from {start} to {end}...")

    sector_rows = sb_select("sector_scores", {"select": "ticker"})
    stock_rows = sb_select("stock_scores", {"select": "ticker"})
    tickers = sorted({r["ticker"] for r in sector_rows} | {r["ticker"] for r in stock_rows} | {"SPY"})

    price_cache: dict[str, pd.Series] = {}
    for ticker in tickers:
        print(f"  Fetching {ticker}...")
        price_cache[ticker] = fetch_price_history(ticker, start, end)

    all_results = []
    all_results += backtest_sector_classification(run_map, price_cache)
    all_results += backtest_regime(run_map, price_cache.get("SPY"))
    all_results += backtest_stock_composite(run_map, price_cache)

    if not all_results:
        print("No backtest results computed — likely not enough historical runs yet.")
        sys.exit(0)

    sb_insert("backtest_results", all_results)

    print("\n=== Results (grouped by metric, horizon) ===")
    for metric in ["sector_classification", "sector_relative_strength", "regime", "stock_composite"]:
        metric_rows = [r for r in all_results if r["metric_type"] == metric]
        if not metric_rows:
            continue
        print(f"\n--- {metric} ---")
        for horizon in BACKTEST_HORIZONS:
            print(f"  [{horizon}]")
            for r in metric_rows:
                if r["horizon"] != horizon:
                    continue
                if r["note"]:
                    print(f"    {r['group_label']:30s} {r['note']}")
                else:
                    print(f"    {r['group_label']:30s} mean fwd return: {r['mean_forward_return_pct']:+.2f}%  (n={r['sample_count']})")

    print("\n=== Done ===")
    print("Reminder: any group above BACKTEST_MIN_SAMPLES is still just a mean, "
          "not a significance-tested result. Treat this as a sanity check, not proof.")


if __name__ == "__main__":
    main()
