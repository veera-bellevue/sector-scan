"""
Configuration for the sector rotation scanner.

Edit SECTOR_ETFS to change which sector ETFs get scanned. Actual holdings
are no longer configured here — they're submitted via docs/upload.html
and stored in Supabase (see fetch_all_holdings_batches() in scan.py), since
free data sources don't reliably expose iShares/Vanguard ETF holdings.
"""

# Benchmark used for the "is the index overbought while defensives lead"
# flag, and for the relative-strength-vs-SPY calculation on each sector.
BENCHMARK = "SPY"

# Sector ETFs to scan, with a short label for readability in reports.
# All iShares (not mixed with Vanguard) so every fund's holdings export uses
# the same CSV layout — see docs/upload.html, which parses that layout
# specifically. Mapped 1:1 from the original Vanguard-based lineup:
#   VDE -> IYE, VGT -> IYW, VHT -> IYH, VPU -> IDU, VIS -> IYJ, VAW -> IYM
#   IYF, IYC, IYK were already iShares and are unchanged.
# Note: sector_scores rows from before this switch (under the old Vanguard
# tickers) remain in Supabase as history but won't get new data going
# forward — backtest.py will see a discontinuity in that ticker's series
# at the switchover date.
SECTOR_ETFS = {
    "IYE": "Energy",
    "IYW": "Technology",
    "IYH": "Healthcare",
    "IDU": "Utilities",
    "IYJ": "Industrials",
    "IYM": "Materials",
    "IYF": "Financials",
    "IYC": "Consumer Discretionary",
    "IYK": "Consumer Staples",
}

# How many top-weighted holdings to actually run composite scoring on, out
# of whatever full holdings list you upload via docs/upload.html. Full lists
# are still stored and diffed in their entirety for weight-change detection
# (a holding falling from #4 to #7 in weight is a real, correctly-detected
# reweight now — it's no longer just falling out of a small manually-typed
# sample window). This only controls the expensive part: how many tickers
# per sector get a live yfinance + fundamentals lookup each run.
HOLDINGS_COUNT = 5

# Technical thresholds
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_BULL_MOMENTUM = 55   # RSI above this + bull SMA structure = "Leading"
RSI_WEAK_MOMENTUM = 50   # RSI below this + bull SMA structure = "Stalling"

# History window to pull
LOOKBACK = "1y"
INTERVAL = "1d"

# Composite score weights (must sum to 1.0)
WEIGHT_TECHNICAL = 0.4
WEIGHT_VALUATION = 0.4
WEIGHT_PATTERN = 0.2

# Only run stock-level scoring for sectors classified as "Leading" this run
# (i.e. where the sector-level scan says money is moving in). Change to
# {"Leading", "Stalling"} if you want to also cover borderline sectors.
HOLDINGS_REQUIRED_FOR = {"Leading"}

# If a Leading sector is missing holdings and already has a pending email
# request, re-send the reminder after this many days rather than staying
# silent.
RENOTIFY_AFTER_DAYS = 3

# When a holding's weight shifts by at least this many percentage points
# between two submitted batches for the same ETF, flag it as a real change
# (not just rounding noise) and include it in the weight-change alert email.
WEIGHT_CHANGE_THRESHOLD_PTS = 2.0

# --- backtest.py settings ---
# Forward-return horizons to check, in trading days (~5/day-week, ~21/month).
BACKTEST_HORIZONS = {"1w": 5, "1m": 21, "3m": 63}

# Don't report a group's average forward return until at least this many
# historical observations exist for it — a mean of 2 data points is noise,
# not evidence, and reporting it with the same formatting as a real result
# would overstate how much this backtest actually knows at low sample sizes.
BACKTEST_MIN_SAMPLES = 10
