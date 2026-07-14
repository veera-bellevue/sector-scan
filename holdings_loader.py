"""
holdings_loader.py

Reads iShares ETF holdings CSVs from a local folder
(C:\\Users\\raghu\\Downloads\\ETF_holdings) and parses them into a
normalized format ready to push into Supabase.

Replaces the docs/upload.html manual-upload flow. Intended to run LOCALLY
(Task Scheduler or manual run) since GitHub Actions cannot see your local
filesystem. This script parses + validates; wire in the Supabase write
call at the bottom once you confirm output looks right.
"""

import csv
import io
import re
from pathlib import Path
from typing import Optional

ETF_TICKERS = ["IYE", "IYW", "IYH", "IDU", "IYJ", "IYM", "IYF", "IYC", "IYK"]

# Known ticker formatting mismatches between iShares CSV exports and
# how your scan universe expects them (fixes the BRK-B/BRKB issue).
TICKER_FIXES = {
    "BRKB": "BRK-B",
    "BRK.B": "BRK-B",
    "BFB": "BF-B",
    "BF.B": "BF-B",
}

# A real holding ticker is short, alphanumeric, and may have . or -
# This rejects the multi-line BlackRock legal disclaimer row, which lands
# entirely in the Ticker field when the footer is parsed as CSV.
VALID_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


def find_file_for_ticker(directory: Path, ticker: str) -> Optional[Path]:
    """Match files named like 'IYE_holdings.csv' (primary convention seen
    in practice), falling back to any CSV containing the ticker in its name."""
    exact = directory / f"{ticker}_holdings.csv"
    if exact.exists():
        return exact

    candidates = [f for f in directory.glob("*.csv") if ticker.lower() in f.name.lower()]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def normalize_ticker(raw_ticker: str) -> str:
    raw_ticker = raw_ticker.strip().upper()
    return TICKER_FIXES.get(raw_ticker, raw_ticker)


def parse_ishares_csv(path: Path) -> list[dict]:
    """Parse an iShares fund-holdings CSV export (metadata rows, then a
    'Ticker,Name,Sector,...' header, then holdings, then a legal disclaimer
    blob at the bottom)."""
    raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw_text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Ticker,Name,"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(f"Could not locate 'Ticker,Name,...' header row in {path.name}")

    csv_body = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_body))

    holdings = []
    skipped = []
    for row in reader:
        ticker_raw = row.get("Ticker")
        weight_raw = row.get("Weight (%)")
        name = row.get("Name")

        if not ticker_raw or not name or weight_raw is None:
            continue  # catches the disclaimer row (only 'Ticker' field populated)

        ticker_candidate = ticker_raw.strip().upper()
        if not VALID_TICKER_RE.match(ticker_candidate):
            skipped.append(ticker_raw[:40])
            continue

        try:
            weight = float(str(weight_raw).replace("%", "").strip())
        except ValueError:
            continue

        holdings.append({
            "ticker": normalize_ticker(ticker_candidate),
            "name": name.strip(),
            "sector": (row.get("Sector") or "").strip(),
            "asset_class": (row.get("Asset Class") or "").strip(),
            "weight_pct": weight,
        })

    if skipped:
        print(f"  (skipped {len(skipped)} non-ticker row(s) in {path.name}, e.g. {skipped[0]!r})")

    return holdings


def sanity_check_sector(etf_ticker: str, holdings: list[dict]) -> None:
    """Catches the IYW/IYF-swap style bug: if the dominant 'Sector' values
    in the file don't look like they belong to this ETF, warn loudly."""
    sectors = [h["sector"] for h in holdings if h["sector"] not in ("", "Cash and/or Derivatives")]
    if not sectors:
        return
    top_sector = max(set(sectors), key=sectors.count)

    expected = {
        "IYE": "Energy", "IYW": "Information Technology", "IYH": "Health Care",
        "IDU": "Utilities", "IYJ": "Industrials", "IYM": "Materials",
        "IYF": "Financial", "IYC": "Consumer Discretionary", "IYK": "Consumer Staples",
    }
    exp = expected.get(etf_ticker, "")
    if exp and exp.lower() not in top_sector.lower():
        print(f"  !! WARNING: {etf_ticker} file's dominant sector is '{top_sector}', "
              f"expected something like '{exp}'. Possible mislabeled/swapped file.")


def load_all_holdings(directory: str = r"C:\Users\raghu\Downloads\ETF_holdings") -> dict[str, list[dict]]:
    dir_path = Path(directory)
    if not dir_path.exists():
        raise FileNotFoundError(f"Holdings directory not found: {directory}")

    results = {}
    missing = []

    for etf_ticker in ETF_TICKERS:
        file_path = find_file_for_ticker(dir_path, etf_ticker)
        if file_path is None:
            missing.append(etf_ticker)
            continue

        holdings = parse_ishares_csv(file_path)
        sanity_check_sector(etf_ticker, holdings)
        results[etf_ticker] = holdings
        print(f"[{etf_ticker}] loaded {len(holdings)} holdings from {file_path.name}")

    if missing:
        print(f"WARNING: no matching file found for: {', '.join(missing)}")

    return results


if __name__ == "__main__":
    data = load_all_holdings()
    for etf, holdings in data.items():
        top = sorted(holdings, key=lambda h: h["weight_pct"], reverse=True)[:3]
        print(f"{etf}: {len(holdings)} holdings | top: {[h['ticker'] for h in top]}")

    # TODO: once output above looks right, add the Supabase write here, e.g.:
    # from supabase import create_client
    # sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    # for etf, holdings in data.items():
    #     sb.table("etf_holdings").upsert([
    #         {"etf": etf, "as_of": today, **h} for h in holdings
    #     ]).execute()
