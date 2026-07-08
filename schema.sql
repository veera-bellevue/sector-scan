-- Run this once in the Supabase SQL editor to set up the scanner's tables.

create table if not exists runs (
    id bigint generated always as identity primary key,
    run_ts timestamptz not null default now()
);

create table if not exists regime_summary (
    id bigint generated always as identity primary key,
    run_id bigint references runs(id) on delete cascade,
    label text,
    pct_leading numeric,
    pct_lagging numeric,
    spy_rsi numeric,
    spy_overbought boolean,
    notes text
);

create table if not exists sector_scores (
    id bigint generated always as identity primary key,
    run_id bigint references runs(id) on delete cascade,
    ticker text,
    sector_name text,
    price numeric,
    rsi numeric,
    sma50 numeric,
    sma200 numeric,
    trend text,
    classification text,
    relative_strength_rsi numeric,
    outperforming_spy boolean
);

create table if not exists stock_scores (
    id bigint generated always as identity primary key,
    run_id bigint references runs(id) on delete cascade,
    ticker text,
    sector_etf text,
    price numeric,
    rsi numeric,
    trend text,
    high_52w numeric,
    low_52w numeric,
    overbought_crossings_1y int,
    pe numeric,
    forward_pe numeric,
    peg numeric,
    technical_score numeric,
    valuation_score numeric,
    pattern_score numeric,
    composite_score numeric,
    rank int
);

-- Indexes for the dashboard's "latest run" queries
create index if not exists idx_sector_scores_run on sector_scores(run_id);
create index if not exists idx_stock_scores_run on stock_scores(run_id);
create index if not exists idx_regime_summary_run on regime_summary(run_id);

-- Row Level Security: allow the dashboard (anon key, read-only) to SELECT,
-- but never INSERT/UPDATE/DELETE. Writes only happen from GitHub Actions
-- using the service_role key, which bypasses RLS entirely.
alter table runs enable row level security;
alter table regime_summary enable row level security;
alter table sector_scores enable row level security;
alter table stock_scores enable row level security;

create policy "public read runs" on runs for select using (true);
create policy "public read regime_summary" on regime_summary for select using (true);
create policy "public read sector_scores" on sector_scores for select using (true);
create policy "public read stock_scores" on stock_scores for select using (true);

-- ---------------------------------------------------------------------------
-- Top holdings: submitted by you via dashboard/upload.html when the scanner
-- emails you asking for a sector's top 3 holdings. Each submission is a
-- "batch" (3 rows sharing a batch_id) so the scanner can always find the
-- most recent complete set per ETF, even if you update it later.
-- ---------------------------------------------------------------------------
create table if not exists top_holdings (
    id bigint generated always as identity primary key,
    etf_ticker text not null,
    holding_ticker text not null,
    rank int not null,
    weight_pct numeric,
    batch_id uuid not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_top_holdings_etf on top_holdings(etf_ticker, created_at desc);

alter table top_holdings enable row level security;

-- The public upload form can INSERT (using the anon key) but cannot read
-- back other people's data. Only the scanner (service_role key, which
-- bypasses RLS) ever reads this table. Note: since the anon key is exposed
-- client-side, anyone with the upload URL could technically submit bogus
-- rows — acceptable for a personal research tool, but keep the URL
-- unlisted rather than posting it publicly.
create policy "anon can submit holdings" on top_holdings
    for insert
    with check (true);

-- ---------------------------------------------------------------------------
-- Holding requests: tracks which ETFs the scanner has already emailed you
-- about, so it doesn't send a new email every single day a sector stays
-- "Leading". Fully private — only the scan job (service_role key) touches it.
-- ---------------------------------------------------------------------------
create table if not exists holding_requests (
    id bigint generated always as identity primary key,
    etf_ticker text not null,
    requested_at timestamptz not null default now(),
    fulfilled boolean not null default false
);
create index if not exists idx_holding_requests_etf on holding_requests(etf_ticker, fulfilled);

alter table holding_requests enable row level security;
-- No policies added on purpose: default-deny for anon/authenticated roles.
-- The service_role key used by scan.py bypasses RLS entirely.

-- ---------------------------------------------------------------------------
-- Holdings weight changes: computed by scan.py each run by diffing the two
-- most recent submitted batches per ETF. Captures new entrants, dropped
-- holdings, and weight shifts past WEIGHT_CHANGE_THRESHOLD_PTS. Public read
-- so the dashboard can display recent changes; only the scanner writes.
-- ---------------------------------------------------------------------------
create table if not exists holdings_weight_changes (
    id bigint generated always as identity primary key,
    run_id bigint references runs(id) on delete cascade,
    etf_ticker text not null,
    holding_ticker text not null,
    change_type text not null check (change_type in ('new_entrant', 'dropped', 'reweighted')),
    prev_weight numeric,
    new_weight numeric,
    delta numeric,
    created_at timestamptz not null default now()
);
create index if not exists idx_weight_changes_run on holdings_weight_changes(run_id);

alter table holdings_weight_changes enable row level security;
create policy "public read holdings_weight_changes" on holdings_weight_changes for select using (true);

-- ---------------------------------------------------------------------------
-- MIGRATION — run this instead if you already created top_holdings from an
-- earlier version of this schema (skip if you're setting up fresh, since the
-- create table above already includes weight_pct).
-- ---------------------------------------------------------------------------
-- alter table top_holdings add column if not exists weight_pct numeric;

-- ---------------------------------------------------------------------------
-- MIGRATION — run this instead if you already created sector_scores from an
-- earlier version of this schema (skip if you're setting up fresh, since the
-- create table above already includes these columns).
-- ---------------------------------------------------------------------------
-- alter table sector_scores add column if not exists relative_strength_rsi numeric;
-- alter table sector_scores add column if not exists outperforming_spy boolean;
