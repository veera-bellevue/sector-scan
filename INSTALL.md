# Installation Guide — Sector Rotation Scan

A checklist-style walkthrough to get this running from zero. Follow the
steps in order — later steps depend on values from earlier ones.

Total time: ~20-30 minutes, all free-tier services.

---

## What you'll need before starting
- A GitHub account
- A Supabase account (free) — supabase.com
- A Resend account (free) — resend.com
- The project files (`sector-scan.zip` from this conversation)

---

## Step 1 — Get the code into a GitHub repo

1. Unzip `sector-scan.zip` locally.
2. Create a new **public or private** GitHub repo (either works — Pages
   works on both, private just needs GitHub Pages enabled with a paid plan
   on some account tiers, so public is simpler if you're on a free account).
3. Push the folder contents to that repo:
   ```bash
   cd sector-scan
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

---

## Step 2 — Set up Supabase (the database)

1. Go to supabase.com → New Project. Pick any name/region, set a database
   password (save it somewhere, though you won't need it directly).
2. Once the project is ready, open the **SQL Editor** (left sidebar).
3. Open `schema.sql` from the project folder, copy its entire contents,
   paste into the SQL Editor, and click **Run**.
   - This creates 7 tables: `runs`, `regime_summary`, `sector_scores`,
     `stock_scores`, `top_holdings`, `holding_requests`,
     `holdings_weight_changes`.
   - Ignore the commented-out migration lines near the bottom — those are
     only for upgrading an *existing* older installation, not needed here.
4. Go to **Project Settings → API**. You'll need three values from this
   page in later steps:
   - **Project URL** (e.g. `https://xxxxx.supabase.co`)
   - **`anon` `public` key** — safe to expose client-side
   - **`service_role` key** — secret, never put this in a client-side file

---

## Step 3 — Set up Resend (email notifications)

1. Create a free account at resend.com.
2. Go to **API Keys** → create a new key. Copy it — you'll only see it once.
3. For personal use, you can send from Resend's shared test sender
   (`onboarding@resend.dev`) without verifying your own domain. If you want
   emails to come from your own address, verify a domain under
   **Domains** first — optional, skip for now if you just want it working.

---

## Step 4 — Configure GitHub Actions secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `SUPABASE_URL` | Project URL from Step 2 |
| `SUPABASE_SERVICE_KEY` | `service_role` key from Step 2 |
| `RESEND_API_KEY` | API key from Step 3 |
| `ALERT_EMAIL_TO` | Your email address |
| `ALERT_EMAIL_FROM` | Optional — leave unset to use Resend's shared sender |
| `UPLOAD_URL` | Leave blank for now — you'll fill this in Step 6 |

The workflow (`.github/workflows/scan.yml`) is already set to run weekdays
at 21:30 UTC (~4:30pm ET). No action needed unless you want a different
time — edit the `cron` line if so.

---

## Step 5 — Host the dashboard and upload page

1. Open `docs/index.html` in a text editor. Find these two lines near
   the bottom and fill them in with your values from Step 2:
   ```js
   const SUPABASE_URL = "YOUR_SUPABASE_URL";
   const SUPABASE_ANON_KEY = "YOUR_SUPABASE_ANON_KEY";
   ```
2. Do the same in `docs/upload.html` (same two lines, same values).
3. Commit and push these changes:
   ```bash
   git add docs/
   git commit -m "Add Supabase keys to dashboard"
   git push
   ```
4. In your GitHub repo: **Settings → Pages** → under "Build and
   deployment," set Source to **Deploy from a branch**, branch `main`,
   folder `/docs`. Save.
   - **Important:** GitHub Pages' branch-deploy option only lets you pick
     `/` (root) or `/docs` — no other folder name is selectable, even
     though the search box looks like it accepts one. That's why this
     project's dashboard files live in a folder named `docs/` rather than
     something more descriptive.
5. GitHub will give you a live URL, typically:
   `https://YOUR_USERNAME.github.io/YOUR_REPO/`
   - The dashboard is at that URL.
   - The upload form is at `.../upload.html`.
   - Give it a minute or two the first time — Pages takes a short while to
     deploy.

---

## Step 6 — Wire the upload link into notification emails

1. Go back to **Settings → Secrets and variables → Actions**.
2. Edit the `UPLOAD_URL` secret you left blank in Step 4, set it to:
   `https://YOUR_USERNAME.github.io/YOUR_REPO/upload.html`

---

## Step 7 — Run it once locally to confirm everything works

Before trusting the schedule, run the whole pipeline yourself once.

```bash
cd sector-scan
pip install -r requirements.txt
```

Then set the environment variables for your shell — **the syntax differs by
platform, and getting this wrong is the single most common setup error**
(it produces a confusing `401 / row violates row-level security policy`
error rather than an obvious "variable not set" error, because the request
still goes through, just without the right permissions).

**macOS / Linux / Git Bash:**
```bash
export SUPABASE_URL="https://xxxxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."          # service_role key
export RESEND_API_KEY="re_..."
export ALERT_EMAIL_TO="you@example.com"
export UPLOAD_URL="https://YOUR_USERNAME.github.io/YOUR_REPO/upload.html"
python scan.py
```

**Windows Command Prompt (cmd.exe) — no quotes around values:**
```cmd
set SUPABASE_URL=https://xxxxx.supabase.co
set SUPABASE_SERVICE_KEY=eyJ...
set RESEND_API_KEY=re_...
set ALERT_EMAIL_TO=you@example.com
set UPLOAD_URL=https://YOUR_USERNAME.github.io/YOUR_REPO/upload.html
python scan.py
```
⚠️ In `cmd.exe`, `set VAR="value"` includes the quote characters *literally*
in the value — unlike bash. Don't quote the values here even though it
feels natural to.

**Windows PowerShell — quotes are fine here:**
```powershell
$env:SUPABASE_URL = "https://xxxxx.supabase.co"
$env:SUPABASE_SERVICE_KEY = "eyJ..."
$env:RESEND_API_KEY = "re_..."
$env:ALERT_EMAIL_TO = "you@example.com"
$env:UPLOAD_URL = "https://YOUR_USERNAME.github.io/YOUR_REPO/upload.html"
python scan.py
```

All three of these only last for your current terminal session — close the
window and you'll need to set them again next time you run it locally
(that's expected; the scheduled runs get their values from GitHub Actions
secrets instead, not from your machine).

You should see console output scanning SPY, each sector ETF, and (if any
sector is Leading with no holdings yet) a note that it would send — or
actually sends — a notification email.

**Verify it worked:**
- In Supabase → **Table Editor**, check `runs` has a new row, and
  `sector_scores` has 9 rows for that `run_id`.
- Check your email for the "needs holdings for" notification, if any
  sector came back Leading.
- Open your dashboard URL — it should show the regime summary and sector
  table populated.

---

## Step 8 — Submit your first holdings

1. Open the upload link (from the email, or directly at `.../upload.html`).
2. Pick the ETF you were notified about.
3. Enter its top 5 holdings by ticker, and optionally their weight %.
4. Submit.
5. Either wait for the next scheduled run, or re-run `python scan.py`
   locally to pick it up immediately and confirm `stock_scores` populates
   in Supabase and on the dashboard.

---

## Step 9 — Let it run on schedule

From here, no more manual steps — GitHub Actions runs `scan.py`
automatically on the cron schedule. Your loop going forward is just:
check your email when it asks for holdings, submit them, check the
dashboard when you want the latest picture.

You can also trigger a run anytime without waiting for the schedule: repo
→ **Actions** tab → **Sector Scan** workflow → **Run workflow**.

---

## Troubleshooting

**Workflow runs but nothing shows up in Supabase**
Check the Actions log (repo → Actions → click the run → click the `scan`
job) for `[error]` lines — usually a wrong/missing secret. Confirm all
required secrets are set and spelled exactly as in Step 4.

**Dashboard shows "No runs found yet"**
Either the workflow hasn't run yet (trigger it manually), or the anon key /
URL in `docs/index.html` doesn't match your Supabase project.

**Upload form errors on submit**
Almost always a mismatched or missing anon key in `docs/upload.html`.
Double check it against Step 2, and make sure you pushed the file with the
key filled in (Step 5.3).

**No email arrives**
Check `ALERT_EMAIL_TO` and `RESEND_API_KEY` are set correctly, and check
your spam folder — mail from a shared sender domain (`onboarding@resend.dev`)
sometimes lands there initially.

**`401` / `new row violates row-level security policy` when running locally**
This means the request reached Supabase but was authenticated as a
low-privilege role (anon), not `service_role`, which is supposed to bypass
RLS entirely. Two likely causes:
1. You copied the **`anon`** key instead of the **`service_role`** key from
   Supabase's API settings page — they sit right next to each other.
2. (Windows `cmd.exe` specifically) You wrapped the value in quotes with
   `set VAR="value"` — cmd.exe includes the quote characters literally in
   the variable, corrupting the key. Don't quote values in `cmd.exe`; use
   PowerShell's `$env:VAR = "value"` if you want to quote them, or drop the
   quotes entirely in `cmd.exe`.

Sanity-check the key before rerunning:
```python
python -c "import os; k=os.environ.get('SUPABASE_SERVICE_KEY',''); print('length:', len(k)); print('starts with eyJ:', k.startswith('eyJ')); print('has stray quotes:', '\"' in k)"
```

**"No results found" when picking a Pages folder**
GitHub Pages only supports `/` (root) or `/docs` as the source folder for
"Deploy from a branch" — typing any other folder name (like `/dashboard`)
will always show "No results found," regardless of whether that folder
actually exists in your repo. This project's dashboard files are named
`docs/` for exactly this reason — pick `/docs` from the dropdown instead.

**"relation does not exist" errors in the Actions log**
The SQL in `schema.sql` didn't fully run — go back to Step 2.3 and re-run
it in the Supabase SQL Editor, checking for red error text after running.
