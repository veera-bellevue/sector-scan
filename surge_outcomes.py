"""
surge_outcomes.py

Automates the "did volume_surge tend to precede a classification
improvement?" eyeball check we've been doing by hand in Supabase's SQL
editor. Run this daily (right after scan.py, same schedule) — it builds a
report and emails it via the same Resend setup scan.py already uses, so
there's no need to open Supabase or check Action logs to see the results.

For sectors: tracks whether a volume_surge day led to classification
improving (Mixed/Stalling/Lagging -> Leading) within a lookforward window.

For stocks: tracks whether a volume_surge day led to composite_score
rising within the same window (stock_scores has no classification field).

    python surge_outcomes.py                  # default 10-run lookforward
    python surge_outcomes.py --lookforward 5   # shorter window
    python surge_outcomes.py --no-email        # print only, skip sending

Requires the same env vars as scan.py: SUPABASE_URL, SUPABASE_SERVICE_KEY,
and (for email) RESEND_API_KEY, ALERT_EMAIL_TO. Read-only against
Supabase — never writes anything back.
"""

import os
import sys
import html
import argparse
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "Sector Scan <onboarding@resend.dev>")

CLASSIFICATION_RANK = {"Lagging": 0, "Mixed": 1, "Stalling": 2, "Leading": 3}


def sb_select(table: str, params: dict) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  [FATAL] no Supabase creds — set SUPABASE_URL / SUPABASE_SERVICE_KEY")
        sys.exit(1)
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code >= 300:
        print(f"  [error] select from {table} failed: {resp.status_code} {resp.text[:300]}")
        return []
    return resp.json()


def fetch_all(table: str, select: str, order: str) -> list[dict]:
    """Paginate through everything — default Supabase REST caps a single
    response at 1000 rows, which you'll eventually hit as history grows."""
    rows, offset, page_size = [], 0, 1000
    while True:
        batch = sb_select(table, {
            "select": select,
            "order": order,
            "limit": page_size,
            "offset": offset,
        })
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def build_run_sequence(runs: list[dict]):
    """run_id -> sequence index (0-based, oldest first), so 'N runs later'
    means N scan.py executions later, not N calendar days later (skips
    weekends/holidays naturally since those are just runs that didn't happen)."""
    ordered = sorted(runs, key=lambda r: r["run_ts"])
    return {r["id"]: i for i, r in enumerate(ordered)}, {i: r["run_ts"] for i, r in enumerate(ordered)}


def analyze_sectors(lookforward: int) -> list[str]:
    """Returns the report as a list of plain-text lines (also used to build
    the email body), one line per printable row."""
    lines = []
    runs = fetch_all("runs", "id,run_ts", "run_ts.asc")
    run_seq, seq_to_ts = build_run_sequence(runs)

    rows = fetch_all("sector_scores",
                      "run_id,ticker,sector_name,classification,rel_volume,volume_surge",
                      "run_id.asc")
    if not rows:
        lines.append("No sector_scores data yet.")
        return lines

    by_ticker = {}
    for r in rows:
        if r["run_id"] not in run_seq:
            continue
        seq = run_seq[r["run_id"]]
        by_ticker.setdefault(r["ticker"], {})[seq] = r

    surge_events = [r for r in rows if r.get("volume_surge") and r["run_id"] in run_seq]

    lines.append(f"=== SECTOR volume_surge outcomes (lookforward={lookforward} runs) ===")
    lines.append(f"Total volume_surge events found: {len(surge_events)}")
    if len(surge_events) < 10:
        lines.append("  [note] fewer than 10 events — too early for a reliable read "
                      "(same BACKTEST_MIN_SAMPLES=10 threshold backtest.py uses). Keep collecting.")

    improved, unchanged, worsened, no_future_data = 0, 0, 0, 0

    for ev in surge_events:
        seq = run_seq[ev["run_id"]]
        ticker = ev["ticker"]
        start_class = ev["classification"]
        target_seq = seq + lookforward
        future_row = by_ticker.get(ticker, {}).get(target_seq)

        if future_row is None:
            no_future_data += 1
            outcome = "(not enough runs elapsed yet)"
        else:
            end_class = future_row["classification"]
            start_rank = CLASSIFICATION_RANK.get(start_class, 1)
            end_rank = CLASSIFICATION_RANK.get(end_class, 1)
            if end_rank > start_rank:
                improved += 1
                outcome = f"IMPROVED ({start_class} -> {end_class})"
            elif end_rank < start_rank:
                worsened += 1
                outcome = f"worsened ({start_class} -> {end_class})"
            else:
                unchanged += 1
                outcome = f"unchanged ({start_class})"

        ts = seq_to_ts.get(seq, "?")
        relvol = ev.get("rel_volume")
        lines.append(f"  {ticker:6s} @ {ts}  rel_vol={relvol}  start={start_class:10s}  -> {outcome}")

    scored = improved + unchanged + worsened
    lines.append("")
    lines.append(f"Summary: {improved} improved, {unchanged} unchanged, {worsened} worsened, "
                  f"{no_future_data} still pending ({lookforward} runs haven't elapsed yet)")
    if scored >= 10:
        pct = round(100 * improved / scored, 1)
        lines.append(f"  -> {pct}% of surge events with enough elapsed history led to classification "
                      f"improvement within {lookforward} runs.")
    return lines


def analyze_stocks(lookforward: int) -> list[str]:
    lines = []
    runs = fetch_all("runs", "id,run_ts", "run_ts.asc")
    run_seq, seq_to_ts = build_run_sequence(runs)

    rows = fetch_all("stock_scores",
                      "run_id,ticker,sector_etf,composite_score,rel_volume,volume_surge",
                      "run_id.asc")
    if not rows:
        lines.append("No stock_scores data yet.")
        return lines

    by_ticker = {}
    for r in rows:
        if r["run_id"] not in run_seq:
            continue
        seq = run_seq[r["run_id"]]
        by_ticker.setdefault(r["ticker"], {})[seq] = r

    surge_events = [r for r in rows if r.get("volume_surge") and r["run_id"] in run_seq]

    lines.append(f"=== STOCK volume_surge outcomes (lookforward={lookforward} runs) ===")
    lines.append(f"Total volume_surge events found: {len(surge_events)}")

    improved, worsened, no_future_data = 0, 0, 0
    for ev in surge_events:
        seq = run_seq[ev["run_id"]]
        ticker = ev["ticker"]
        start_score = ev["composite_score"]
        target_seq = seq + lookforward
        future_row = by_ticker.get(ticker, {}).get(target_seq)

        if future_row is None or start_score is None or future_row.get("composite_score") is None:
            no_future_data += 1
            outcome = "(not enough runs elapsed yet)"
        else:
            delta = future_row["composite_score"] - start_score
            if delta > 0:
                improved += 1
                outcome = f"composite_score +{delta:.1f}"
            else:
                worsened += 1
                outcome = f"composite_score {delta:.1f}"

        ts = seq_to_ts.get(seq, "?")
        lines.append(f"  {ticker:6s} @ {ts}  rel_vol={ev.get('rel_volume')}  -> {outcome}")

    scored = improved + worsened
    lines.append("")
    lines.append(f"Summary: {improved} improved, {worsened} worsened, {no_future_data} still pending")
    if scored >= 10:
        pct = round(100 * improved / scored, 1)
        lines.append(f"  -> {pct}% of surge events with enough elapsed history saw composite_score rise "
                      f"within {lookforward} runs.")
    return lines


def build_email_html(sector_lines: list[str], stock_lines: list[str]) -> str:
    body = "\n".join(sector_lines) + "\n\n\n" + "\n".join(stock_lines)
    escaped = html.escape(body)
    return (
        "<div style=\"font-family:monospace;white-space:pre-wrap;"
        "font-size:13px;line-height:1.4;\">" + escaped + "</div>"
    )


def send_email(subject: str, html_body: str) -> bool:
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        print("  [skip] no email credentials configured (RESEND_API_KEY / ALERT_EMAIL_TO) — "
              "not sending, printing to console instead.")
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
    print("  Surge outcomes email sent.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Track outcomes of volume_surge events")
    parser.add_argument("--lookforward", type=int, default=10,
                         help="Number of runs ahead to check for outcome (default 10)")
    parser.add_argument("--no-email", action="store_true",
                         help="Print the report only, skip sending it by email")
    args = parser.parse_args()

    sector_lines = analyze_sectors(args.lookforward)
    stock_lines = analyze_stocks(args.lookforward)

    for line in sector_lines:
        print(line)
    print()
    for line in stock_lines:
        print(line)

    if not args.no_email:
        subject = f"Sector Scan: volume surge outcomes (lookforward={args.lookforward})"
        html_body = build_email_html(sector_lines, stock_lines)
        send_email(subject, html_body)


if __name__ == "__main__":
    main()
