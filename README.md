# Sector Rotation Scan

Automates the sector/stock rotation framework:

1. Pulls RSI + trend data for a set of sector ETFs + SPY, classifies each
   sector as Leading / Stalling / Lagging (i.e. where money is moving), and
   separately computes each sector's RSI *relative to SPY* to flag whether
   it's actually outperforming the index, not just moving in its own
   absolute uptrend.
2. For any **Leading** sector it doesn't yet have holdings for, emails you
   asking you to submit that sector's top 5 holdings (with optional weight
   %) via a web form.
3. Once submitted, the next run automatically scores those stocks
   (technicals + valuation + pattern reliability), and compares the new
   submission against your previous one to flag new entrants, dropped
   holdings, or meaningful weight shifts — emailing you if anything crosses
   the threshold.
4. A dashboard reads the latest run: sector classifications, relative
   strength vs. SPY, weight changes, and the ranked stock list.

## What it does NOT do
- It does not place trades or connect to your brokerage. It's read-only
  market data + scoring.
- It does not predict returns. The composite score reflects setup quality
  (trend health, valuation vs. growth, historical overbought round-trips),
  not a forecast.
- It does not auto-discover ETF holdings or their real-time weights — free
  data sources don't reliably expose iShares/Vanguard holdings, which is
  why this uses an email-and-upload flow instead of guessing.

## 1. Set up Supabase
1. Create a free project at supabase.com.
2. Open the SQL editor, paste in `schema.sql`, run it. This creates:
   - `runs`, `regime_summary`, `sector_scores`, `stock_scores` — the scan
     results (public read-only, written by the service key). `sector_scores`
     includes `relative_strength_rsi` and `outperforming_spy`.
   - `top_holdings` — where your uploads land, including `weight_pct`
     (public **insert-only**, read only by the service key)
   - `holding_requests` — internal tracking so you don't get emailed daily
     about the same sector (fully private)
   - `holdings_weight_changes` — new/dropped/reweighted holdings detected
     each run (public read, so the dashboard can show it)
3. **If you already ran an earlier version of this schema**, don't re-run
   the whole file — instead run just the two migration blocks near the
   bottom of `schema.sql` (adding `relative_strength_rsi`/`outperforming_spy`
   to `sector_scores`, and `weight_pct` to `top_holdings` + the new
   `holdings_weight_changes` table).
4. From Project Settings → API, copy:
   - the **Project URL**
   - the **`service_role` key** (secret — used by GitHub Actions to read/write everything)
   - the **`anon` key** (public — used by the dashboard to read, and the
     upload form to insert)

## 2. Set up email (Resend)
1. Create a free account at resend.com and get an API key. (Any transactional
   email API works — Resend is used here because setup is a single API key,
   but you can swap the `send_email()` function in `scan.py` for SendGrid,
   Postmark, etc. if you prefer.)
2. Check their current free-tier limits and verified-sender requirements —
   for personal use you can typically send from their shared test domain
   without verifying your own domain.

## 3. Set up GitHub
1. Push this folder to a new GitHub repo.
2. In the repo, go to Settings → Secrets and variables → Actions, add:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `RESEND_API_KEY`
   - `ALERT_EMAIL_TO` — your email address
   - `ALERT_EMAIL_FROM` — optional, defaults to Resend's shared test sender
   - `UPLOAD_URL` — the link to `upload.html` once it's hosted (step 4),
     e.g. `https://youruser.github.io/sector-scan/upload.html`
3. The workflow in `.github/workflows/scan.yml` runs weekdays at 21:30 UTC
   (~4:30pm ET). Adjust the cron expression if you want a different time,
   or trigger it manually from the Actions tab (`workflow_dispatch`).

## 4. Set up the dashboard + upload page
1. Open `dashboard/index.html` and `dashboard/upload.html`, fill in both:
   ```js
   const SUPABASE_URL = "https://xxxx.supabase.co";
   const SUPABASE_ANON_KEY = "eyJ...";   // the anon/public key, NOT service_role
   ```
2. Host both for free with GitHub Pages: Settings → Pages → deploy from the
   `dashboard/` folder.
3. Go back and set the `UPLOAD_URL` secret (step 3) to the live
   `upload.html` link, then re-run the scan once so future emails include it.
4. Security note: the anon key only has permission to SELECT on the scan
   result tables and INSERT on `top_holdings` — it can't overwrite past
   runs or read your other data. Still, keep the upload URL unlisted rather
   than posting it somewhere public, since anyone with the link could submit
   holdings.

## 5. The day-to-day loop
1. Scanner runs, sees e.g. IYK and VHT are Leading, has no holdings on file
   → emails you a list + a link to `upload.html`.
2. You open the link, pick the ETF, type in the top 5 tickers (and
   optionally their weight %), hit submit.
3. Next scheduled run picks up what you submitted and scores those stocks.
4. If you submit an update later (say IYK's weights shifted after a
   rebalance), `scan.py` diffs it against your previous submission for that
   ETF and emails you separately about anything that moved by more than
   `WEIGHT_CHANGE_THRESHOLD_PTS` (2 points by default) — new entrants,
   dropped holdings, and significant re-weights all get flagged.
5. If a sector stops being Leading, it's simply skipped again — no action
   needed. If it becomes Leading again later, your most recent submission
   is reused automatically.

## 6. Relative strength vs. SPY
Alongside the existing absolute classification (which only looks at a
sector's own RSI/SMA), each sector also gets:
- `relative_strength_rsi` — RSI computed on the sector-price/SPY-price
  ratio, so it reflects whether the sector is gaining *relative* to the
  index, not just in absolute terms
- `outperforming_spy` — `true` when that relative RSI is ≥ 50

These are shown side by side with the absolute classification on the
dashboard — a sector can be "Leading" in absolute terms while still
underperforming SPY on a relative basis, and now you can see both.

## 7. Weight tracking
`upload.html` has an optional weight % field next to each ticker. When
filled in, every submission is compared against the *previous* submission
for that same ETF, and `scan.py` flags:
- **new_entrant** — a ticker that wasn't in the last batch
- **dropped** — a ticker that was, but isn't anymore
- **reweighted** — a ticker present in both, whose weight moved by at least
  `WEIGHT_CHANGE_THRESHOLD_PTS` (default 2.0 percentage points, in `config.py`)

Detected changes are stored in `holdings_weight_changes`, shown on the
dashboard, and trigger their own email — separate from the missing-holdings
email — so a rebalance doesn't get buried. Weight % is optional; leaving it
blank just means that ticker is skipped in the diff (no false positives from
missing data).

## 8. Run it locally first
Before relying on the schedule, run it once yourself to confirm the whole
pipeline works end to end:
```bash
pip install -r requirements.txt
export SUPABASE_URL="https://xxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."   # service_role key
export RESEND_API_KEY="re_..."
export ALERT_EMAIL_TO="you@example.com"
export UPLOAD_URL="https://youruser.github.io/sector-scan/upload.html"
python scan.py
```
You should see console output for each ticker, then a ranked summary. Check
the Supabase table editor to confirm rows landed, then open the dashboard
to confirm it reads them.

## Extending it
- **More sectors/holdings**: edit `SECTOR_ETFS` in `config.py`.
- **Different scoring weights**: `WEIGHT_TECHNICAL` / `WEIGHT_VALUATION` /
  `WEIGHT_PATTERN` in `config.py` (must sum to 1.0).
- **Different weight-change sensitivity**: `WEIGHT_CHANGE_THRESHOLD_PTS` in
  `config.py`.
- **Alerts instead of a pull dashboard**: add a step to `scan.py` that posts
  the top-line regime summary to a Slack/Discord webhook — the regime
  dict is already computed in `main()`, easy to forward.
- **Backtesting the framework itself**: since every run is timestamped and
  stored, you can later join `regime_summary` history against actual SPY
  returns to see whether "Defensive rotation" calls preceded real
  drawdowns or were false positives (like the 2019 case) — this is the
  single most useful thing to build next.
