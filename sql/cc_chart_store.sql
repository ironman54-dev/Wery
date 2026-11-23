-- Record of the SQL applied to Supabase project mmbdijtyvgzhmnlcddki on 24 Nov 2026 for the stock charts, the daily NSE price store and the
-- data-health indicator. (The waitlist and Contact functions are in cc_backend.sql.) Safe to re-run.
-- Also in the project: Edge Function `bars-append` (supabase/functions/bars-append/index.ts) and pg_cron jobs
--   keepalive-daily      17 3 * * *      (08:47 IST) calls cc_waitlist_count through the public API
--   bars-append-evening  0 14 * * 1-5    (19:30 IST) calls the bars-append function
--   bars-append-morning  30 2 * * 2-6    (08:00 IST) backup call

create table if not exists public.cc_secrets (name text primary key, hash text not null);   -- one-time bulk-load token (hash only)
create table if not exists public.cc_bars (
  symbol text not null, yr int not null,
  d int[] not null, o int[] not null, h int[] not null, l int[] not null, c int[] not null, v bigint[] not null,
  primary key (symbol, yr));                       -- raw NSE prices in paise, one row per stock per year; d = days since 1970-01-01
create table if not exists public.cc_events (symbol text not null, d date not null, f double precision not null, primary key (symbol, d)); -- split/bonus factors
create table if not exists public.cc_event_review (id bigserial primary key, symbol text not null, d date not null, ratio double precision, note text,
  created_at timestamptz not null default now(), resolved boolean not null default false, unique (symbol, d));
create table if not exists public.cc_health (id bigserial primary key, ts timestamptz not null default now(), source text not null, ok boolean not null, issues jsonb not null default '[]'::jsonb);
create index if not exists cc_health_source_ts on public.cc_health (source, ts desc);
alter table public.cc_secrets enable row level security;
alter table public.cc_bars enable row level security;
alter table public.cc_events enable row level security;
alter table public.cc_event_review enable row level security;
alter table public.cc_health enable row level security;
-- no policies: the browser reaches the data only through the functions below.

-- Functions (cc_can_write, cc_put_bars, cc_put_events, cc_put_review, cc_health_put, cc_bars_status, cc_stock_bars, cc_review_list,
-- cc_health_status) were created through Supabase migrations "chart_bars_store", "stock_bars_since_param" and "data_health_indicator".
-- Read them with:  select proname, pg_get_functiondef(oid) from pg_proc where proname like 'cc\_%' and pronamespace = 'public'::regnamespace;
-- Writers (cc_put_*, cc_health_put) accept only the project's service role or the bulk-load token; readers (cc_stock_bars, cc_bars_status,
-- cc_health_status) are public and return data that is public anyway (end-of-day prices).
