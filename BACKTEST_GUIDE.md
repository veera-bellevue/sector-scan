# Implementation Guide — Backtesting the Scanner's Own Calls

This covers setting up and using `backtest.py` specifically. It assumes
`scan.py` is already installed and running per `INSTALL.md` — this is an
add-on, not a separate app.

---

## Step 1 — Apply the schema migration

`backtest.py` writes its results to a table that didn't exist in earlier
versions of `schema.sql`. In the Supabase SQL Editor, run:

```sql
create table if not exists backtest_results (
    id bigint generated always as identity primary key,
    computed_at timestamptz not null default now(),
    metric_type text not null,
    group_label text not null,
    horizon text not null,
    mean_forward_return_pct numeric,
    sample_count int not null,
    note text
);
create index if not exists idx_backtest_results_computed on backtest_results(computed_at desc);

alter table backtest_results enable row level security;
create policy "public read backtest_results" on backtest_results for select using (true);
```

(If you're setting the whole project up fresh from the current `schema.sql`,
this table is already included — no separate step needed.)

---

## Step 2 — Confirm you have `backtest.py` and its workflow file

From the project root, you should have:
```
backtest.py
.github/workflows/backtest.yml
```
No new secrets needed — it reuses `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`,
the same ones `scan.py` already uses.

---

## Step 3 — Do NOT run it yet if you're on fewer than ~15-20 scan runs

Check how many rows are in the `runs` table (Supabase → Table Editor →
`runs`). If it's under ~15-20, running the backtest now will produce almost
entirely "insufficient data" output — technically correct, but not useful
yet. Come back to this step once `scan.py` has been running on its weekday
schedule for at least a few weeks.

If you want to see the mechanics work anyway (not real results, just to
confirm the plumbing), skip ahead to Step 4 — it's harmless to run early,
it'll just say so honestly.

---

## Step 4 — Run it locally

Same environment variables as `scan.py`:

**macOS / Linux / Git Bash:**
```bash
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."     # service_role key
python backtest.py
```

**Windows Command Prompt:**
```cmd
set SUPABASE_URL=https://xxxxx.supabase.co
set SUPABASE_SERVICE_KEY=eyJ...
python backtest.py
```

**Windows PowerShell:**
```powershell
$env:SUPABASE_URL = "https://xxxxx.supabase.co"
$env:SUPABASE_SERVICE_KEY = "eyJ..."
python backtest.py
```

---

## Step 5 — Reading the output

You'll see console output grouped by metric type and horizon:

```
--- sector_classification ---
  [1w]
    Leading                        insufficient data (n=4, need >= 10)
    Lagging                        insufficient data (n=2, need >= 10)
  [1m]
    Leading                        mean fwd return: +1.85%  (n=12)
    Lagging                        mean fwd return: -0.42%  (n=11)
```

**What each field means:**
- `mean fwd return` — average % price change over the horizon, starting
  from the date each historical run classified that group
- `n=` — how many observations that average is based on
- `insufficient data` — below `BACKTEST_MIN_SAMPLES` (10 by default); the
  script deliberately withholds a mean here rather than showing you a
  number computed from 2-3 data points

**What a "good" result looks like:** `Leading` sectors showing a
meaningfully higher mean forward return than `Lagging` sectors, consistently
across horizons, with both groups well past the minimum sample size. One
horizon looking good and another looking flat or reversed is not that —
it's more likely noise, and worth waiting for more data rather than reading
into it.

**What to actually do with a bad result:** if `Leading` doesn't outperform
`Lagging` once you have real sample sizes, that's a signal the RSI/SMA
thresholds in `config.py` (`RSI_BULL_MOMENTUM`, `RSI_WEAK_MOMENTUM`) may
need retuning — not necessarily that the whole approach is broken. Try
adjusting the thresholds and see if a fresh set of runs (going forward —
you can't retroactively reclassify old runs) trends differently.

---

## Step 6 — Check the results in Supabase / build a dashboard view later

Every run's results also land in `backtest_results`, so you have a growing
history rather than a one-off printout. There's no dashboard section for
this yet — `docs/index.html` doesn't query this table. If you want one,
the pattern to follow is the same as the "Holdings Weight Changes" section
already in `docs/index.html`: a `sb.from("backtest_results").select("*")`
call filtered to the most recent `computed_at`, rendered as a table.

---

## Step 7 — Turn on the schedule once it's actually useful

`backtest.yml` is intentionally **not** on a cron schedule out of the box —
running it weekly while you only have a handful of `scan.py` runs just
produces repeated "insufficient data" noise. Once Step 5 is showing real
numbers you trust:

1. Open `.github/workflows/backtest.yml`
2. Uncomment the `schedule:` block near the top (e.g. Friday evenings)
3. Commit and push

From then on it runs unattended alongside `scan.py`, building up
`backtest_results` history automatically.

---

## Limits worth remembering

- This checks whether *scan.py's* classifications predicted anything. It
  has no way to backtest the value-investing side of the original
  framework (the moat checklist / margin-of-safety calculator) — that tool
  was never wired into the automated pipeline or logged anywhere, so
  there's nothing to backtest there yet.
- No significance testing, no correction for sectors/stocks moving together
  (if tech rallies, most "Leading" tags that week are riding the same wave,
  not independent bets) — treat a wide, consistent gap across 50+
  observations as suggestive, not proof.
- You cannot retroactively fix a bad classification threshold and have it
  apply to past runs — tuning `config.py` only changes future scans. Old
  `sector_scores` rows keep whatever classification they were given at the
  time.
