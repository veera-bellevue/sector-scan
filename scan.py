"""
Sector rotation scanner.

Pulls sector ETF + constituent stock data via yfinance, computes RSI/SMA
trend classification and a composite technical+valuation score, then writes
a timestamped snapshot to Supabase so a dashboard can read the latest run
and its history.

Run manually:
    python scan.py

Run in CI (GitHub Actions):
    Requires env vars SUPABASE_URL and SUPABASE_SERVICE_KEY (see README.md)
"""

import os
import sys
import time
import datetime
import requests
import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    BENCHMARK, SECTOR_ETFS, RSI_PERIOD, HOLDINGS_COUNT,
    RSI_OVERBOUGHT, RSI_OVERSOLD, RSI_BULL_MOMENTUM, RSI_WEAK_MOMENTUM,
    LOOKBACK, INTERVAL, WEIGHT_TECHNICAL, WEIGHT_VALUATION, WEIGHT_PATTERN,
    HOLDINGS_REQUIRED_FOR, RENOTIFY_AFTER_DAYS, WEIGHT_CHANGE_THRESHOLD_PTS,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "Sector Scan <onboarding@resend.dev>")
UPLOAD_URL = os.environ.get("UPLOAD_URL", "")  # e.g. https://youruser.github.io/sector-scan/upload.html


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Standard Wilder RSI. Handles the all-gains/all-losses edge case
    (constant-direction series) explicitly instead of dividing by zero."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rsi = pd.Series(np.nan, index=close.index)
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    loss_zero = (avg_loss == 0) & (avg_gain > 0)
    normal = (avg_loss > 0)

    rsi[normal] = 100 - (100 / (1 + avg_gain[normal] / avg_loss[normal]))
    rsi[loss_zero] = 100.0
    rsi[both_zero] = 50.0  # flat price, no movement either way
    return rsi


def fetch_history(ticker: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(ticker).history(period=LOOKBACK, interval=INTERVAL)
        if df.empty or len(df) < 60:
            print(f"  [warn] not enough history for {ticker}")
            return None
        return df
    except Exception as e:
        print(f"  [error] fetching {ticker}: {e}")
        return None


def classify_trend(price: float, sma50: float, sma200: float) -> str:
    if price > sma50 and price > sma200:
        return "BULL"
    if price < sma50 and price < sma200:
        return "BEAR"
    return "MIXED"


def classify_sector(rsi: float, trend: str) -> str:
    if trend == "BULL" and rsi >= RSI_BULL_MOMENTUM:
        return "Leading"
    if trend == "BULL" and rsi < RSI_WEAK_MOMENTUM:
        return "Stalling"
    if trend == "BEAR":
        return "Lagging"
    return "Mixed"


def rsi_overbought_history_flag(rsi_series: pd.Series) -> int:
    """How many times RSI touched >=70 in the lookback window — a rough
    proxy for 'this name tends to round-trip from overbought' (LLY/PM style)."""
    above = rsi_series >= RSI_OVERBOUGHT
    # count distinct crossings into overbought territory
    crossings = ((above) & (~above.shift(1).fillna(False))).sum()
    return int(crossings)


# ---------------------------------------------------------------------------
# Fundamentals (best-effort; yfinance fundamentals can be missing/None)
# ---------------------------------------------------------------------------

def fetch_fundamentals(ticker: str) -> dict:
    out = {"pe": None, "forward_pe": None, "peg": None, "profit_margin": None}
    try:
        info = yf.Ticker(ticker).info
        out["pe"] = info.get("trailingPE")
        out["forward_pe"] = info.get("forwardPE")
        out["peg"] = info.get("pegRatio") or info.get("trailingPegRatio")
        out["profit_margin"] = info.get("profitMargins")
    except Exception as e:
        print(f"  [warn] fundamentals for {ticker}: {e}")
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def technical_score(rsi: float, trend: str) -> float:
    """0-100. Rewards bull trend + healthy (not overextended) RSI."""
    if trend == "BEAR":
        base = 20
    elif trend == "MIXED":
        base = 45
    else:
        base = 65
    # Distance-from-50 penalty once overbought, since the framework treats
    # "stretched" (>70) as a caution flag, not a positive
    if rsi >= RSI_OVERBOUGHT:
        base -= (rsi - RSI_OVERBOUGHT) * 1.5
    elif rsi < RSI_OVERSOLD:
        base -= (RSI_OVERSOLD - rsi) * 1.0
    elif rsi >= RSI_BULL_MOMENTUM:
        base += (rsi - RSI_BULL_MOMENTUM) * 0.8
    return round(max(0, min(100, base)), 1)


def valuation_score(pe: float | None, forward_pe: float | None, peg: float | None) -> float:
    """0-100, best-effort. Missing data returns a neutral 50."""
    if pe is None and peg is None:
        return 50.0
    score = 50.0
    if peg is not None:
        # PEG ~1 = fairly valued reference point
        score += (1.0 - peg) * 15
    if pe is not None and forward_pe is not None and pe > 0:
        # cheaper forward vs trailing = improving earnings outlook
        score += (pe - forward_pe) / pe * 40
    return round(max(0, min(100, score)), 1)


def pattern_score(overbought_crossings: int) -> float:
    """0-100. Fewer overbought round-trips this year = more reliable trend."""
    score = 100 - (overbought_crossings * 20)
    return round(max(0, min(100, score)), 1)


def composite(tech: float, val: float, pat: float) -> float:
    return round(
        tech * WEIGHT_TECHNICAL + val * WEIGHT_VALUATION + pat * WEIGHT_PATTERN, 1
    )


# ---------------------------------------------------------------------------
# Supabase writes
# ---------------------------------------------------------------------------

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb_insert(table: str, rows: list[dict]):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print(f"  [skip] no Supabase creds — would have inserted {len(rows)} rows into {table}")
        return None
    if not rows:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = requests.post(url, headers=sb_headers(), json=rows, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] insert into {table} failed: {resp.status_code} {resp.text[:300]}")
        resp.raise_for_status()
    return resp.json()


def sb_select(table: str, params: dict) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {k: v for k, v in sb_headers().items() if k != "Prefer"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] select from {table} failed: {resp.status_code} {resp.text[:300]}")
        return []
    return resp.json()


def sb_update(table: str, match_params: dict, data: dict):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = requests.patch(url, headers=sb_headers(), params=match_params, json=data, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] update {table} failed: {resp.status_code} {resp.text[:300]}")


def create_run() -> int | None:
    result = sb_insert("runs", [{"run_ts": datetime.datetime.utcnow().isoformat()}])
    if result:
        return result[0]["id"]
    return None


# ---------------------------------------------------------------------------
# Holdings lookup + email notification
# ---------------------------------------------------------------------------

def fetch_all_holdings_batches() -> dict[str, list[dict]]:
    """
    Returns {etf_ticker: [batch, batch, ...]}, newest batch first, where each
    batch is {"batch_id", "created_at", "holdings": [{"ticker","rank","weight_pct"}]}.
    A single query for the whole table — cheap enough at this scale and avoids
    N per-ETF round trips.
    """
    rows = sb_select("top_holdings", {
        "select": "etf_ticker,holding_ticker,rank,weight_pct,batch_id,created_at",
        "order": "created_at.desc",
    })
    grouped: dict[str, dict[str, dict]] = {}
    order: dict[str, list[str]] = {}
    for r in rows:
        etf = r["etf_ticker"]
        bid = r["batch_id"]
        grouped.setdefault(etf, {})
        order.setdefault(etf, [])
        if bid not in grouped[etf]:
            grouped[etf][bid] = {"batch_id": bid, "created_at": r["created_at"], "holdings": []}
            order[etf].append(bid)
        grouped[etf][bid]["holdings"].append({
            "ticker": r["holding_ticker"], "rank": r["rank"], "weight_pct": r.get("weight_pct"),
        })

    out = {}
    for etf, bids in order.items():
        batches = [grouped[etf][b] for b in bids]
        for b in batches:
            b["holdings"].sort(key=lambda h: h["rank"])
        out[etf] = batches
    return out


def latest_holdings_map(all_batches: dict[str, list[dict]]) -> dict[str, list[str]]:
    """{etf: [ticker, ...]} from just the most recent batch per ETF — this is
    what the stock-scoring loop actually needs."""
    return {
        etf: [h["ticker"] for h in batches[0]["holdings"]]
        for etf, batches in all_batches.items() if batches
    }


def detect_weight_changes(all_batches: dict[str, list[dict]],
                           threshold_pts: float = WEIGHT_CHANGE_THRESHOLD_PTS) -> list[dict]:
    """
    Diffs the two most recent batches per ETF (only runs where both exist and
    at least one side has a weight_pct on record). Flags:
      - new_entrant: appeared in the latest batch, wasn't in the previous one
      - dropped: was in the previous batch, missing from the latest one
      - reweighted: present in both, weight shifted >= threshold_pts
    """
    changes = []
    for etf, batches in all_batches.items():
        if len(batches) < 2:
            continue
        latest = {h["ticker"]: h["weight_pct"] for h in batches[0]["holdings"]}
        previous = {h["ticker"]: h["weight_pct"] for h in batches[1]["holdings"]}
        for ticker in set(latest) | set(previous):
            in_latest, in_previous = ticker in latest, ticker in previous
            if in_latest and not in_previous:
                changes.append({
                    "etf_ticker": etf, "holding_ticker": ticker, "change_type": "new_entrant",
                    "prev_weight": None, "new_weight": latest[ticker], "delta": None,
                })
            elif in_previous and not in_latest:
                changes.append({
                    "etf_ticker": etf, "holding_ticker": ticker, "change_type": "dropped",
                    "prev_weight": previous[ticker], "new_weight": None, "delta": None,
                })
            else:
                new_w, old_w = latest[ticker], previous[ticker]
                if new_w is not None and old_w is not None:
                    delta = round(new_w - old_w, 2)
                    if abs(delta) >= threshold_pts:
                        changes.append({
                            "etf_ticker": etf, "holding_ticker": ticker, "change_type": "reweighted",
                            "prev_weight": old_w, "new_weight": new_w, "delta": delta,
                        })
    return changes


def weight_change_email_html(changes: list[dict]) -> str:
    def row(c):
        if c["change_type"] == "new_entrant":
            desc = f"newly appeared at {c['new_weight']}%" if c["new_weight"] is not None else "newly appeared (no weight given)"
        elif c["change_type"] == "dropped":
            desc = f"dropped out (was {c['prev_weight']}%)" if c["prev_weight"] is not None else "dropped out"
        else:
            sign = "+" if c["delta"] >= 0 else ""
            desc = f"{c['prev_weight']}% &rarr; {c['new_weight']}% ({sign}{c['delta']} pts)"
        return f"<li><b>{c['holding_ticker']}</b> in {c['etf_ticker']} — {desc}</li>"

    return f"""
    <p>Comparing your latest holdings submission against the previous one,
    these changes crossed the {WEIGHT_CHANGE_THRESHOLD_PTS}pt threshold:</p>
    <ul>{"".join(row(c) for c in changes)}</ul>
    <p>This usually means either you updated the weights intentionally, or
    the underlying ETF actually rebalanced — worth a quick sanity check
    against the provider's fact sheet if a number looks large.</p>
    """


def fetch_pending_requests() -> dict[str, str]:
    """Returns {etf_ticker: requested_at} for unfulfilled requests."""
    rows = sb_select("holding_requests", {
        "select": "etf_ticker,requested_at",
        "fulfilled": "eq.false",
        "order": "requested_at.desc",
    })
    out = {}
    for r in rows:
        if r["etf_ticker"] not in out:  # keep most recent per etf
            out[r["etf_ticker"]] = r["requested_at"]
    return out


def send_email(subject: str, html_body: str) -> bool:
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        print("  [skip] no email credentials configured (RESEND_API_KEY / ALERT_EMAIL_TO) — "
              "would have sent:")
        print(f"    subject: {subject}")
        return False
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": ALERT_EMAIL_FROM, "to": [ALERT_EMAIL_TO], "subject": subject, "html": html_body},
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"  [error] email send failed: {resp.status_code} {resp.text[:300]}")
        return False
    print("  Notification email sent.")
    return True


def handle_missing_holdings(sector_rows: list[dict], holdings_map: dict[str, list[str]]) -> None:
    """
    Compares which sectors currently need holdings (per HOLDINGS_REQUIRED_FOR)
    against what's been uploaded (holdings_map, already fetched by the
    caller). Emails you about any newly-missing or stale-pending sectors, and
    marks fulfilled requests as resolved.

    Important: a sector is only marked "already notified" (written to
    holding_requests) if the email actually sent successfully. If Resend
    errors out, nothing is persisted, so the very next run will try again
    immediately instead of silently waiting out RENOTIFY_AFTER_DAYS for an
    email you never received.
    """
    pending = fetch_pending_requests()
    now = datetime.datetime.utcnow()

    needed = [r["ticker"] for r in sector_rows if r["classification"] in HOLDINGS_REQUIRED_FOR]
    missing = [etf for etf in needed if not holdings_map.get(etf)]

    # Resolve requests for sectors that now have holdings
    for etf, _ in list(pending.items()):
        if holdings_map.get(etf):
            sb_update("holding_requests", {"etf_ticker": f"eq.{etf}", "fulfilled": "eq.false"},
                      {"fulfilled": True})
            pending.pop(etf, None)

    newly_missing = []
    stale_pending = []
    for etf in missing:
        if etf not in pending:
            newly_missing.append(etf)
        else:
            requested_at = datetime.datetime.fromisoformat(pending[etf].replace("Z", "+00:00")).replace(tzinfo=None)
            if (now - requested_at).days >= RENOTIFY_AFTER_DAYS:
                stale_pending.append(etf)

    to_notify = newly_missing + stale_pending
    if not to_notify:
        return

    names = ", ".join(f"{etf} ({SECTOR_ETFS.get(etf, etf)})" for etf in to_notify)
    link = UPLOAD_URL or "(set UPLOAD_URL secret to include a direct link)"
    html = f"""
    <p>Money is currently moving into these sectors, but I don't have their
    top {HOLDINGS_COUNT} holdings yet, so stock-level scoring is being
    skipped for them this run:</p>
    <ul>{"".join(f"<li><b>{etf}</b> — {SECTOR_ETFS.get(etf, etf)}</li>" for etf in to_notify)}</ul>
    <p>Upload the top {HOLDINGS_COUNT} holdings for each here: <a href="{link}">{link}</a></p>
    <p>Once submitted, the next scheduled run will pick them up automatically.</p>
    """
    sent = send_email(f"Sector scan needs holdings for: {names}", html)

    if not sent:
        print("  [warn] email failed — not marking these sectors as notified, "
              "will retry next run instead of waiting for RENOTIFY_AFTER_DAYS")
        return

    if newly_missing:
        sb_insert("holding_requests", [
            {"etf_ticker": etf, "requested_at": now.isoformat(), "fulfilled": False}
            for etf in newly_missing
        ])
    for etf in stale_pending:
        sb_update("holding_requests", {"etf_ticker": f"eq.{etf}", "fulfilled": "eq.false"},
                  {"requested_at": now.isoformat()})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def analyze_ticker(ticker: str, df: pd.DataFrame | None = None,
                    spy_close: pd.Series | None = None) -> dict | None:
    if df is None:
        df = fetch_history(ticker)
    if df is None:
        return None
    close = df["Close"]
    price = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else float(close.mean())
    rsi_series = compute_rsi(close)
    rsi = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0
    trend = classify_trend(price, sma50, sma200)
    crossings = rsi_overbought_history_flag(rsi_series.dropna())

    # --- Relative strength vs SPY (additive, doesn't change absolute classification) ---
    relative_strength_rsi = None
    outperforming_spy = None
    if spy_close is not None:
        aligned = pd.concat([close, spy_close], axis=1, join="inner")
        aligned.columns = ["sector", "spy"]
        if len(aligned) >= RSI_PERIOD + 1:
            ratio = aligned["sector"] / aligned["spy"]
            rel_rsi_series = compute_rsi(ratio)
            last_rel_rsi = rel_rsi_series.iloc[-1]
            if not np.isnan(last_rel_rsi):
                relative_strength_rsi = round(float(last_rel_rsi), 1)
                # RSI>=50 on the sector/SPY ratio means the ratio's recent
                # gains outweigh its losses — i.e. the sector is outpacing
                # SPY more often than not over the RSI window.
                outperforming_spy = relative_strength_rsi >= 50

    return {
        "price": round(price, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "rsi": round(rsi, 1),
        "trend": trend,
        "overbought_crossings_1y": crossings,
        "high_52w": round(float(close.max()), 2),
        "low_52w": round(float(close.min()), 2),
        "relative_strength_rsi": relative_strength_rsi,
        "outperforming_spy": outperforming_spy,
    }


def main():
    print(f"=== Sector scan run @ {datetime.datetime.utcnow().isoformat()} UTC ===")
    run_id = create_run()
    print(f"run_id = {run_id}")

    # --- Benchmark ---
    spy_df = fetch_history(BENCHMARK)
    if spy_df is None:
        print("Could not fetch benchmark, aborting.")
        sys.exit(1)
    bench = analyze_ticker(BENCHMARK, df=spy_df)
    spy_close = spy_df["Close"]
    print(f"{BENCHMARK}: price={bench['price']} rsi={bench['rsi']} trend={bench['trend']}")

    # --- Sectors ---
    sector_rows = []
    leading, lagging = 0, 0
    for etf, label in SECTOR_ETFS.items():
        print(f"Scanning sector {etf} ({label})...")
        data = analyze_ticker(etf, spy_close=spy_close)
        if data is None:
            continue
        classification = classify_sector(data["rsi"], data["trend"])
        if classification == "Leading":
            leading += 1
        elif classification == "Lagging":
            lagging += 1
        sector_rows.append({
            "run_id": run_id,
            "ticker": etf,
            "sector_name": label,
            "price": data["price"],
            "rsi": data["rsi"],
            "sma50": data["sma50"],
            "sma200": data["sma200"],
            "trend": data["trend"],
            "classification": classification,
            "relative_strength_rsi": data["relative_strength_rsi"],
            "outperforming_spy": data["outperforming_spy"],
        })
        time.sleep(0.3)  # be polite to the free data source

    total_sectors = len(sector_rows) if sector_rows else 1
    pct_leading = round(100 * leading / total_sectors, 1)
    pct_lagging = round(100 * lagging / total_sectors, 1)

    regime = "Defensive rotation" if pct_leading >= 30 and pct_lagging >= 15 else \
             "Cyclical / risk-on" if pct_leading < 20 else "Mixed / no clear regime"
    spy_overbought = bench["rsi"] >= RSI_OVERBOUGHT
    notes = (
        f"SPY RSI {bench['rsi']} "
        f"({'overbought' if spy_overbought else 'not overbought'}) "
        f"while {pct_leading}% of sectors classified Leading and {pct_lagging}% Lagging."
    )
    print(f"Regime: {regime} | {notes}")

    sb_insert("regime_summary", [{
        "run_id": run_id,
        "label": regime,
        "pct_leading": pct_leading,
        "pct_lagging": pct_lagging,
        "spy_rsi": bench["rsi"],
        "spy_overbought": spy_overbought,
        "notes": notes,
    }])
    sb_insert("sector_scores", sector_rows)

    # --- Figure out which sectors need holdings, notify if missing ---
    all_batches = fetch_all_holdings_batches()
    holdings_map = latest_holdings_map(all_batches)
    handle_missing_holdings(sector_rows, holdings_map)
    active_etfs = [r["ticker"] for r in sector_rows if r["classification"] in HOLDINGS_REQUIRED_FOR]
    print(f"Sectors requiring holdings this run: {active_etfs}")
    print(f"Holdings available for: {[e for e in active_etfs if holdings_map.get(e)]}")

    # --- Detect and report weight changes vs. the previous submission ---
    weight_changes = detect_weight_changes(all_batches)
    if weight_changes:
        print(f"Detected {len(weight_changes)} weight change(s): {weight_changes}")
        sb_insert("holdings_weight_changes", [{**c, "run_id": run_id} for c in weight_changes])
        etf_list = ", ".join(sorted({c["etf_ticker"] for c in weight_changes}))
        send_email(f"Holdings weight changes detected: {etf_list}", weight_change_email_html(weight_changes))
    else:
        print("No weight changes detected vs. previous submission.")

    # --- Constituent stocks (only for sectors we actually have holdings for) ---
    stock_rows = []
    for etf in active_etfs:
        holdings = holdings_map.get(etf, [])
        if not holdings:
            continue  # notified above, will pick up next run once uploaded
        for ticker in holdings:
            print(f"Scanning stock {ticker} (top holding of {etf})...")
            data = analyze_ticker(ticker)
            if data is None:
                continue
            fund = fetch_fundamentals(ticker)
            tech = technical_score(data["rsi"], data["trend"])
            val = valuation_score(fund["pe"], fund["forward_pe"], fund["peg"])
            pat = pattern_score(data["overbought_crossings_1y"])
            comp = composite(tech, val, pat)
            stock_rows.append({
                "run_id": run_id,
                "ticker": ticker,
                "sector_etf": etf,
                "price": data["price"],
                "rsi": data["rsi"],
                "trend": data["trend"],
                "high_52w": data["high_52w"],
                "low_52w": data["low_52w"],
                "overbought_crossings_1y": data["overbought_crossings_1y"],
                "pe": fund["pe"],
                "forward_pe": fund["forward_pe"],
                "peg": fund["peg"],
                "technical_score": tech,
                "valuation_score": val,
                "pattern_score": pat,
                "composite_score": comp,
            })
            time.sleep(0.3)

    # rank within this run
    stock_rows.sort(key=lambda r: r["composite_score"], reverse=True)
    for i, row in enumerate(stock_rows, start=1):
        row["rank"] = i

    sb_insert("stock_scores", stock_rows)

    print("=== Done ===")
    for row in stock_rows:
        print(f"  #{row['rank']} {row['ticker']:6s} composite={row['composite_score']:5.1f} "
              f"(tech={row['technical_score']}, val={row['valuation_score']}, pattern={row['pattern_score']})")


if __name__ == "__main__":
    main()
