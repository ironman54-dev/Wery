"""Daily price bars for the stock charts (raw NSE prices + split/bonus events), stored in Supabase.

Why raw + events: split/bonus adjustment never changes old rows. The chart applies the events in the browser, so a
new split next year can never corrupt stored history.

Commands
  build  --out FILE        10 years of raw OHLCV for the screener universe from the local NSE bhavcopy archive
                           (Video 2 / Video 3 backtest folders) plus a top-up from NSE's public archive.
  load   --in FILE         push a built file to Supabase (needs CC_INGEST_TOKEN in the environment or --token-file)
  append                   weekly: fetch NSE bhavcopy files newer than the last stored date, detect split/bonus
                           events from the exchange's own previous-close, push to Supabase.

Only public NSE files are used. The ingest token is a write key for three Supabase functions; it is never stored in the repo.
"""
import argparse, csv, gzip, io, json, os, pickle, sys, time, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
import urllib.request

import pandas as pd

HERE = Path(__file__).resolve().parent
UNIVERSE_CSV = HERE / "nse_1000_constituents.csv"
SB = "https://mmbdijtyvgzhmnlcddki.supabase.co"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1tYmRpanR5dmd6aG1ubGNkZGtpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTU0NTgxNDIsImV4cCI6MjExMTAzNDE0Mn0."
        "0OQcGYMjAlAKWCUGXHjTd1zDcxid5MCdQFGZmGC_jvI")
V2 = Path("D:/D/ClaudeCode/thecapitalcode/Video 2 - We Followed Indias Smart Money/backtest/data/raw")
V3P = Path("D:/D/ClaudeCode/thecapitalcode/Video 3 - 5 Famous Market Rules Tested/backtest/data/processed/prices_all_adj.parquet")
START = pd.Timestamp("2016-11-24")
SERIES_OK = ("EQ", "BE", "BZ", "SM", "ST")
EPOCH = date(1970, 1, 1)


def universe():
    with UNIVERSE_CSV.open(encoding="utf-8") as f:
        return [r["Symbol"].strip() for r in csv.DictReader(f) if r["Symbol"].strip() and not r["Symbol"].startswith("DUMMY")]


# ---------------------------------------------------------------- reading NSE files
def _get(url, tries=4):
    """GET with a browser User-Agent. 404 means 'no such file' (None). 403/429/5xx mean NSE is throttling: wait and retry."""
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(5 * (k + 1))
        except Exception:
            time.sleep(3 * (k + 1))
    return None


def _dates(col):
    c = col.astype(str).str.strip()
    d = pd.to_datetime(c, format="%d-%b-%Y", errors="coerce")
    return d.fillna(pd.to_datetime(c, format="%d-%b-%y", errors="coerce"))


CLEAN = sorted({a / b for b in range(2, 13) for a in range(1, b)} | {1 / 20, 1 / 25, 1 / 50, 1 / 100})


def clean_fraction(r, tol=0.12):
    """Nearest 'real-world' split/bonus fraction (0.5 for 1:1 bonus, 0.2 for 5:1 split, ...) if r is within tol of one."""
    best = min(CLEAN, key=lambda f: abs(r / f - 1))
    return best if abs(r / best - 1) <= tol else None


def yahoo_splits(symbol, days=120):
    """Splits and bonuses Yahoo lists for the symbol in the last `days` days: {iso_date: price_factor}. Empty on any failure."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?range=6mo&interval=1d&events=split"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        j = json.loads(urllib.request.urlopen(req, timeout=30).read())
        sp = (j["chart"]["result"][0].get("events") or {}).get("splits") or {}
    except Exception:
        return None   # unknown (network), different from "no split"
    out = {}
    for v in sp.values():
        n, d = v.get("numerator"), v.get("denominator")
        if n and d:
            out[datetime.utcfromtimestamp(v["date"]).date().isoformat()] = d / n
    return out


def detect_events(a):
    """a: new rows (symbol, date, c, prev_c). A split/bonus is applied only when TWO independent signals agree:
    (1) the raw price gap matches a clean fraction, and (2) Yahoo lists a split/bonus on that date with the same factor.
    NSE's PREV_CLOSE in the bhavcopy is NOT adjusted, so it cannot be used. Returns (applied, review)."""
    applied, review = {}, []
    r = a["c"] / a["prev_c"]
    cand = a[((r < 0.78) & r.notna()) | (r > 1.7)]
    cache = {}
    for row, rt in zip(cand.itertuples(), r[cand.index]):
        f = clean_fraction(float(rt)) if rt < 1 else (lambda g: (1 / g) if g else None)(clean_fraction(1 / float(rt)))
        if not f:
            continue
        d = row.date.date().isoformat()
        if row.symbol not in cache:
            cache[row.symbol] = yahoo_splits(row.symbol)
        ys = cache[row.symbol]
        yf = ys.get(d) if ys else None
        if yf and abs(yf / f - 1) <= 0.01:
            applied.setdefault(row.symbol, []).append((d, round(yf, 6)))
            print("event applied", row.symbol, d, round(yf, 4), "(price gap", round(float(rt), 3), "+ Yahoo split)")
        else:
            review.append((row.symbol, d, round(float(rt), 4), f, "yahoo unreachable" if ys is None else "no matching Yahoo split"))
            print("REVIEW (gap but no confirmation)", row.symbol, d, round(float(rt), 4))
    return applied, review


def _norm_old(df):
    df.columns = [c.strip().upper() for c in df.columns]
    return pd.DataFrame({
        "symbol": df["SYMBOL"].str.strip(), "series": df["SERIES"].str.strip(),
        "date": _dates(df["TIMESTAMP"]),
        "o": df["OPEN"], "h": df["HIGH"], "l": df["LOW"], "c": df["CLOSE"], "pc": df["PREVCLOSE"],
        "v": df["TOTTRDQTY"], "isin": df.get("ISIN"),
    })


def _norm_new(df):
    df.columns = [c.strip().upper() for c in df.columns]
    for c in ("SYMBOL", "SERIES", "DATE1"):
        df[c] = df[c].astype(str).str.strip()
    num = lambda k: pd.to_numeric(df[k].astype(str).str.strip(), errors="coerce")
    return pd.DataFrame({
        "symbol": df["SYMBOL"], "series": df["SERIES"], "date": _dates(df["DATE1"]),
        "o": num("OPEN_PRICE"), "h": num("HIGH_PRICE"), "l": num("LOW_PRICE"), "c": num("CLOSE_PRICE"),
        "pc": num("PREV_CLOSE"), "v": num("TTL_TRD_QNTY"), "isin": None,
    })


def load_renames(current):
    """NSE's own symbol-change list (old -> new). Returns {old_symbol: final_current_symbol} for symbols that still exist today."""
    raw = _get("https://nsearchives.nseindia.com/content/equities/symbolchange.csv")
    if raw is None:
        raise SystemExit("NSE symbol-change list unavailable; try again in a few minutes")
    raw = raw.decode("utf-8", "ignore")
    nxt = {}
    for r in csv.DictReader(io.StringIO(raw)):
        o, n = (r.get("OLD SYMBOL") or "").strip(), (r.get("NEW SYMBOL") or "").strip()
        if o and n and o != n:
            nxt[o] = n
    out = {}
    for o in nxt:
        t, hops = o, 0
        while t in nxt and hops < 8:
            t, hops = nxt[t], hops + 1
        if t in current and o not in current:
            out[o] = t
    return out


IDX_DIR = Path("D:/D/ClaudeCode/thecapitalcode/Video 2 - We Followed Indias Smart Money/backtest/data/raw/indices")
INDEX_SYMBOL = "NIFTY500"


def _idx_rows(df, day):
    """Nifty 500 row of one NSE index file. The file's own date is checked against the day it was published for:
    some years write it as dd-mm-yyyy and some as mm-dd-yyyy, so both are accepted, but the bar is dated by the day itself."""
    df.columns = [c.strip() for c in df.columns]
    df = df[df["Index Name"].str.strip().str.lower() == "nifty 500"]
    if df.empty:
        return None
    raw = df["Index Date"].astype(str).str.strip().iloc[0]
    want = {f"{day:%d-%m-%Y}", f"{day:%m-%d-%Y}"}
    if raw not in want:
        return None   # a copy of another day's file
    f = lambda k: pd.to_numeric(df[k], errors="coerce")
    return pd.DataFrame({"date": [pd.Timestamp(day)], "o": f("Open Index Value").values, "h": f("High Index Value").values,
                         "l": f("Low Index Value").values, "c": f("Closing Index Value").values, "v": 0})


def fetch_index_day(d):
    raw = _get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv")
    if raw is None:
        return None
    try:
        return _idx_rows(pd.read_csv(io.BytesIO(raw), encoding="latin-1"), d)
    except Exception:
        return None


def read_index_local():
    fr = []
    for p in sorted(IDX_DIR.glob("ind_close_all_*.csv")):
        try:
            day = datetime.strptime(p.stem.split("_")[-1], "%d%m%Y").date()
            r = _idx_rows(pd.read_csv(p, encoding="latin-1"), day)
            if r is not None:
                fr.append(r)
        except Exception:
            pass
    x = pd.concat(fr).drop_duplicates("date").sort_values("date")
    return x[x["date"] >= START]


def read_local():
    frames = []
    for p in sorted((V2 / "bhavcopy_old").glob("cm*bhav.csv.zip")):
        try:
            with zipfile.ZipFile(p) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), index_col=False)
            df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
            frames.append(_norm_old(df))
        except Exception as e:
            print("skip", p.name, e)
    for p in sorted((V2 / "bhavcopy_local").glob("sec_bhavdata_full_*.csv")):
        try:
            frames.append(_norm_new(pd.read_csv(p, index_col=False)))
        except Exception as e:
            print("skip", p.name, e)
    d = pd.concat(frames, ignore_index=True)
    d = d[(d["date"] >= START) & d["series"].isin(SERIES_OK)]
    return d


def fetch_nse_day(d: date):
    raw = _get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
    if raw is None:
        return None   # holiday / no session / not published yet
    try:
        df = _norm_new(pd.read_csv(io.BytesIO(raw), index_col=False, encoding="latin-1"))
    except Exception:
        return None   # a file in an unexpected layout is skipped, never guessed
    return df[df["series"].isin(SERIES_OK)]


# ---------------------------------------------------------------- build
def build(out):
    syms = set(universe())
    local = read_local()
    print("local rows", len(local), local["date"].min().date(), local["date"].max().date())
    ref = pd.read_parquet(V3P, columns=["date", "symbol", "series", "isin", "factor", "cumf"])
    ref = ref[ref["date"] >= START]
    # one row per (symbol, date): prefer EQ, then the others
    rank = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4}
    local["r"] = local["series"].map(rank)
    local = local.sort_values(["symbol", "date", "r"]).drop_duplicates(["symbol", "date"])
    ref["r"] = ref["series"].map(rank)
    ref = ref.sort_values(["symbol", "date", "r"]).drop_duplicates(["symbol", "date"])
    m = local.drop(columns=["isin", "series", "r"]).merge(ref[["date", "symbol", "isin", "factor", "cumf"]], on=["date", "symbol"], how="left")
    last_ref = ref["date"].max()
    # current symbol per ISIN = the symbol traded on the latest date
    cur = ref.sort_values("date").groupby("isin").tail(1).set_index("isin")["symbol"]
    m["cur"] = m["isin"].map(cur).fillna(m["symbol"])
    renames = load_renames(syms)
    print("symbol renames known:", len(renames))
    unresolved = ~m["cur"].isin(syms)
    m.loc[unresolved, "cur"] = m.loc[unresolved, "symbol"].map(renames).fillna(m.loc[unresolved, "cur"])
    m = m[m["cur"].isin(syms)]
    # drop a symbol's bars before it was a different company (ISIN changed under the same symbol): keep the ISIN of the latest bar
    latest_isin = m.sort_values("date").groupby("cur").tail(1).set_index("cur")["isin"]
    m = m[(m["isin"] == m["cur"].map(latest_isin)) | m["isin"].isna()]
    print("rows in universe", len(m), "symbols", m["cur"].nunique())

    # events from the existing adjustment table (splits / bonuses only)
    ev = ref[(ref["factor"] != 1) & ref["factor"].notna()]
    events = {}
    for r in ev.itertuples():
        s = cur.get(r.isin) if isinstance(r.isin, str) else None
        s = s or renames.get(r.symbol, r.symbol)
        if s in syms:
            events.setdefault(s, []).append((r.date.date().isoformat(), float(r.factor)))

    # top-up from NSE's public archive after the reference table ends
    gap_days = []
    d = (m["date"].max() + pd.Timedelta(days=1)).date()
    today = date.today()
    while d <= today:
        gap_days.append(d)   # weekends too: Muhurat and other special sessions
        d += timedelta(days=1)
    add = []
    for d in gap_days:
        df = fetch_nse_day(d)
        if df is None:
            continue
        add.append(df)
        time.sleep(0.4)
    if add:
        a = pd.concat(add, ignore_index=True)
        a["r"] = a["series"].map(rank)
        a = a.sort_values(["symbol", "date", "r"]).drop_duplicates(["symbol", "date"])
        a = a[a["symbol"].isin(syms)].copy()
        a["cur"] = a["symbol"]
        a = a.sort_values(["symbol", "date"])
        a["prev_c"] = a.groupby("symbol")["c"].shift(1)
        lastc = m.sort_values("date").groupby("cur").tail(1).set_index("cur")["c"]
        a["prev_c"] = a["prev_c"].fillna(a["symbol"].map(lastc))
        applied, review = detect_events(a)
        for sy, lst in applied.items():
            events.setdefault(sy, []).extend(lst)
        m = pd.concat([m, a[["date", "symbol", "o", "h", "l", "c", "pc", "v", "cur"]]], ignore_index=True)
        print("top-up days", len(add), "to", a["date"].max().date())

    m = m.dropna(subset=["o", "h", "l", "c"])
    m = m[(m["c"] > 0) & (m["h"] >= m["l"])]
    m = m.sort_values(["cur", "date"])
    bars = {}
    for s, g in m.groupby("cur"):
        bars[s] = {
            "d": [(x.date() - EPOCH).days for x in g["date"]],
            "o": [int(round(v * 100)) for v in g["o"]], "h": [int(round(v * 100)) for v in g["h"]],
            "l": [int(round(v * 100)) for v in g["l"]], "c": [int(round(v * 100)) for v in g["c"]],
            "v": [int(v) if pd.notna(v) else 0 for v in g["v"]],
        }
    # days missing inside the local archive (files never saved, or special Saturday/Sunday sessions such as Muhurat)
    from concurrent.futures import ThreadPoolExecutor
    have = set(m["date"].dt.date)
    last_local = max(have)
    cand, d0 = [], START.date()
    while d0 <= last_local:
        if d0 not in have:
            cand.append(d0)
        d0 += timedelta(days=1)
    with ThreadPoolExecutor(3) as ex:
        found = [(dd, df) for dd, df in ex.map(lambda dd: (dd, fetch_nse_day(dd)), cand) if df is not None and len(df) > 500]
    if found:
        g = pd.concat([df for _, df in found], ignore_index=True)
        g["r"] = g["series"].map(rank)
        g = g.sort_values(["symbol", "date", "r"]).drop_duplicates(["symbol", "date"])
        g["cur"] = g["symbol"].map(renames).fillna(g["symbol"])
        g = g[g["cur"].isin(syms)]
        # NSE's older archive serves the previous session's file for a non-session date: keep only rows that are really new
        known = m[["cur", "date"]].drop_duplicates()
        g = g.merge(known, on=["cur", "date"], how="left", indicator=True)
        g = g[g["_merge"] == "left_only"].drop(columns="_merge")
        m = pd.concat([m, g[["date", "symbol", "o", "h", "l", "c", "pc", "v", "cur"]]], ignore_index=True)
        new_days = sorted({x.date().isoformat() for x in g["date"]})
        print("sessions that were missing locally and are now filled:", len(new_days), new_days[:40])
    filled_days = {pd.Timestamp(x).date() for x in new_days} if found else set()

    # events checked by hand against NSE's own data (the automatic rule would only have put them in the review list)
    vf = HERE / "verified_events.json"
    if vf.exists():
        for e in json.loads(vf.read_text(encoding="utf-8")):
            lst = events.setdefault(e["s"], [])
            if not any(x[0] == e["d"] for x in lst):
                lst.append((e["d"], float(e["f"])))
    ix = read_index_local()
    d = ix["date"].max().date() + timedelta(days=1)
    more = []
    while d <= date.today():
        r = fetch_index_day(d)
        if r is not None:
            more.append(r)
            time.sleep(0.2)
        d += timedelta(days=1)
    ixd = set(ix["date"].dt.date) | {x["date"].dt.date.iloc[0] for x in more}
    need = sorted((have | filled_days) - ixd)
    with ThreadPoolExecutor(3) as ex:
        more += [r for r in ex.map(fetch_index_day, need) if r is not None]
    if more:
        ix = pd.concat([ix] + more).drop_duplicates("date").sort_values("date")
    ix = ix.dropna(subset=["o", "h", "l", "c"])
    bars[INDEX_SYMBOL] = {"d": [(x.date() - EPOCH).days for x in ix["date"]], "o": [int(round(v * 100)) for v in ix["o"]],
                          "h": [int(round(v * 100)) for v in ix["h"]], "l": [int(round(v * 100)) for v in ix["l"]],
                          "c": [int(round(v * 100)) for v in ix["c"]], "v": [0] * len(ix)}
    print("Nifty 500 index bars", len(ix), ix["date"].min().date(), ix["date"].max().date())
    with gzip.open(out, "wb") as f:
        pickle.dump({"bars": bars, "events": events, "built": datetime.now().isoformat()}, f)
    n = sum(len(b["d"]) for b in bars.values())
    print("symbols", len(bars), "bars", n, "events", sum(len(v) for v in events.values()), "->", out)


# ---------------------------------------------------------------- push to Supabase
def rpc(fn, body, token_hdr=None):
    req = urllib.request.Request(f"{SB}/rest/v1/rpc/{fn}", data=json.dumps(body).encode(),
                                 headers={"apikey": ANON, "Authorization": "Bearer " + ANON, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=120).read() or "null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{fn} failed: {e.code} {e.read()[:300]!r}")


def token(args):
    t = os.environ.get("CC_INGEST_TOKEN")
    if not t and getattr(args, "token_file", None):
        t = Path(args.token_file).read_text().strip()
    if not t:
        raise SystemExit("CC_INGEST_TOKEN missing")
    return t


def push(data, tok, only_after=None):
    bars, events = data["bars"], data["events"]
    rows = []
    for s, b in bars.items():
        years = {}
        for i, dd in enumerate(b["d"]):
            y = (EPOCH + timedelta(days=dd)).year
            years.setdefault(y, []).append(i)
        for y, idx in years.items():
            if only_after is not None and (EPOCH + timedelta(days=b["d"][idx[-1]])) <= only_after:
                continue
            rows.append({"s": s, "y": y, **{k: [b[k][i] for i in idx] for k in "dohlcv"}})
    print("year-rows to push", len(rows))
    for i in range(0, len(rows), 40):
        rpc("cc_put_bars", {"p_token": tok, "p_rows": rows[i:i + 40]})
    ev = [{"s": s, "d": d, "f": f} for s, lst in events.items() for d, f in lst]
    for i in range(0, len(ev), 400):
        rpc("cc_put_events", {"p_token": tok, "p_rows": ev[i:i + 400]})
    print("events pushed", len(ev))


def load(args):
    with gzip.open(args.infile, "rb") as f:
        data = pickle.load(f)
    push(data, token(args))


def append(args):
    """Weekly: add the bars NSE published since the last stored date."""
    tok = token(args)
    st = rpc("cc_bars_status", {})
    last = EPOCH + timedelta(days=int(st["last_day"]))
    print("last stored bar", last)
    syms = set(universe())
    rank = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4}
    frames, d = [], last + timedelta(days=1)
    while d <= date.today():
        df = fetch_nse_day(d)
        if df is not None:
            frames.append(df)
            time.sleep(0.4)
        d += timedelta(days=1)
    if not frames:
        print("no new NSE files (holiday or not published yet)")
        return
    a = pd.concat(frames, ignore_index=True)
    a["r"] = a["series"].map(rank)
    a = a.sort_values(["symbol", "date", "r"]).drop_duplicates(["symbol", "date"])
    a = a[a["symbol"].isin(syms)].sort_values(["symbol", "date"]).copy()
    lastc = {k: v / 100 for k, v in st["last_close"].items()}
    a["prev_c"] = a.groupby("symbol")["c"].shift(1)
    a["prev_c"] = a["prev_c"].fillna(a["symbol"].map(lastc))
    events, review = detect_events(a)
    a = a.dropna(subset=["o", "h", "l", "c"])
    bars = {}
    for s, g in a.groupby("symbol"):
        bars[s] = {"d": [(x.date() - EPOCH).days for x in g["date"]],
                   "o": [int(round(v * 100)) for v in g["o"]], "h": [int(round(v * 100)) for v in g["h"]],
                   "l": [int(round(v * 100)) for v in g["l"]], "c": [int(round(v * 100)) for v in g["c"]],
                   "v": [int(v) if pd.notna(v) else 0 for v in g["v"]]}
    # cc_put_bars merges by date inside a year, so sending only the new bars is safe
    push({"bars": bars, "events": events}, tok)
    print("appended", sum(len(b["d"]) for b in bars.values()), "bars up to", a["date"].max().date())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", required=True)
    l = sub.add_parser("load"); l.add_argument("--in", dest="infile", required=True); l.add_argument("--token-file")
    a = sub.add_parser("append"); a.add_argument("--token-file")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.out)
    elif args.cmd == "load":
        load(args)
    else:
        append(args)
