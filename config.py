"""
Configuration for the sector rotation scanner.

Edit SECTOR_ETFS to change which sector ETFs get scanned. Actual holdings
are no longer configured here — they're submitted via dashboard/upload.html
and stored in Supabase (see fetch_all_holdings_batches() in scan.py), since
free data sources don't reliably expose iShares/Vanguard ETF holdings.
"""

# Benchmark used for the "is the index overbought while defensives lead"
# flag, and for the relative-strength-vs-SPY calculation on each sector.
BENCHMARK = "SPY"

# Sector ETFs to scan, with a short label for readability in reports
SECTOR_ETFS = {
    "VDE": "Energy",
    "VGT": "Technology",
    "VHT": "Healthcare",
    "VPU": "Utilities",
    "VIS": "Industrials",
    "VAW": "Materials",
    "IYF": "Financials",
    "IYC": "Consumer Discretionary",
    "IYK": "Consumer Staples",
}

# How many top holdings to request per ETF via the upload form. 5 balances
# coverage (catching a holding that's grown into real weight even if it
# wasn't originally #1-3) against how much manual entry the form asks of
# you. Must match HOLDINGS_COUNT in dashboard/upload.html if you change it —
# that file hardcodes 5 input rows rather than reading this value, since
# it's a static HTML page with no build step.
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
