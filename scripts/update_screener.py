import csv
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "nse_1000_constituents.csv"
OUT_DIR = BASE_DIR.parent / "public" / "data"
HIST_DIR = OUT_DIR / "history"
OUT_DIR.mkdir(parents=True, exist_ok=True)
HIST_DIR.mkdir(parents=True, exist_ok=True)

INDUSTRY_TO_SECTOR = {
    "Metals & Mining": "Metals",
    "Automobile and Auto Components": "Auto",
    "Healthcare": "Pharma & Healthcare",
    "Oil Gas & Consumable Fuels": "Energy & Power",
    "Power": "Energy & Power",
    "Capital Goods": "Infra & CapGoods",
    "Construction": "Infra & CapGoods",
    "Construction Materials": "Infra & CapGoods",
    "Financial Services": "Financials",
    "Information Technology": "IT",
    "Fast Moving Consumer Goods": "FMCG",
    "Consumer Durables": "Consumer",
    "Consumer Services": "Consumer",
    "Realty": "Realty",
    "Media Entertainment & Publication": "Media",
    "Chemicals": "Chemicals",
    "Telecommunication": "Telecom",
    "Textiles": "Textiles",
    "Services": "Services",
    "Diversified": "Diversified",
}


def safe_pct(cur: float, prev: float) -> float:
    return round(((cur / prev) - 1.0) * 100.0, 2) if prev else 0.0


def load_universe() -> list[dict]:
    universe = []
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = (row.get("Symbol") or "").strip()
            ind = (row.get("Industry") or "").strip()
            name = (row.get("Company Name") or sym).strip()
            if not sym or sym.startswith("DUMMY"):
                continue
            if any(k in name.upper() for k in ("REIT", "REAL ESTATE TRUST", "INVIT", "INFRASTRUCTURE INVESTMENT TRUST")):
                continue   # REITs / InvITs are not operating companies: the 8-rule template is not meant for them
            universe.append({
                "symbol": sym,
                "yf_ticker": f"{sym}.NS",
                "company": name,
                "industry": ind,
                "sector": INDUSTRY_TO_SECTOR.get(ind, "Other" if ind in ("", "Other") else ind),
            })
    return universe


def _download(chunk: list[str], tries: int = 3) -> pd.DataFrame:
    for k in range(tries):
        try:
            df = yf.download(chunk, period="3y", interval="1d", progress=False, auto_adjust=True, threads=True)
            if not df.empty:
                return df
        except Exception as e:  # network / Yahoo hiccup: retry
            print(f"download error ({e}); retry {k + 1}/{tries}")
        time.sleep(5 * (k + 1))
    return pd.DataFrame()


def download_batched(tickers: list[str], batch_size: int = 120):
    """Adjusted closes and volumes for all tickers. Returns (closes, volumes)."""
    closes, vols = [], []
    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        df = _download(chunk)
        if df.empty:
            print(f"WARNING: batch {i // batch_size + 1} returned nothing")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            closes.append(df["Close"])
            vols.append(df["Volume"])
        else:
            closes.append(df[["Close"]].rename(columns={"Close": chunk[0]}))
            vols.append(df[["Volume"]].rename(columns={"Volume": chunk[0]}))
    if not closes:
        return pd.DataFrame(), pd.DataFrame()
    c = pd.concat(closes, axis=1)
    v = pd.concat(vols, axis=1)
    return c.loc[:, ~c.columns.duplicated()], v.loc[:, ~v.columns.duplicated()]


# ---------------------------------------------------------------- primary data source: NSE end-of-day store in Supabase
# Raw NSE bhavcopy prices + a short list of split/bonus events (see scripts/bars_pipeline.py and the bars-append function).
# Prices are adjusted for splits and bonuses only, the same way the Video 7 backtest adjusted them (no dividend adjustment).
SB = "https://mmbdijtyvgzhmnlcddki.supabase.co"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1tYmRpanR5dmd6aG1ubGNkZGtpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTU0NTgxNDIsImV4cCI6MjExMTAzNDE0Mn0."
        "0OQcGYMjAlAKWCUGXHjTd1zDcxid5MCdQFGZmGC_jvI")   # public anon key by design; the store only answers read functions to it
CLEAN = sorted({a / b for b in range(2, 13) for a in range(1, b)} | {1 / 20, 1 / 25, 1 / 50, 1 / 100})


def clean_fraction(r, tol):
    best = min(CLEAN, key=lambda f: abs(r / f - 1))
    return best if abs(r / best - 1) <= tol else None


def store_rpc(fn, body, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(f"{SB}/rest/v1/rpc/{fn}", data=json.dumps(body).encode(),
                                         headers={"apikey": ANON, "Authorization": "Bearer " + ANON, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except Exception as e:
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"{fn} failed: {last}")


def _adjusted(raw):
    if not raw or not raw.get("d"):
        return None, None
    idx = pd.to_datetime(raw["d"], unit="D")
    c = pd.Series(raw["c"], index=idx, dtype="float64") / 100.0
    v = pd.Series(raw["v"], index=idx, dtype="float64")
    f = pd.Series(1.0, index=idx)
    for dte, fac in sorted((pd.Timestamp(e[0]), float(e[1])) for e in (raw.get("ev") or [])):
        f[idx < dte] *= fac          # a split on dte changes every older price
    return c * f, v / f


def load_store(universe):
    """Returns (n500_close, closes_df, volumes_df, info). Raises if the store is unreachable or incomplete."""
    st = store_rpc("cc_bars_status", {})
    since = (date.today() - timedelta(days=1250) - date(1970, 1, 1)).days
    syms = [u["symbol"] for u in universe]

    def get(sym):
        return sym, store_rpc("cc_stock_bars", {"p_symbol": sym, "p_since": since})

    with ThreadPoolExecutor(12) as ex:
        res = dict(ex.map(get, ["NIFTY500"] + syms))
    n500, _ = _adjusted(res.pop("NIFTY500"))
    if n500 is None or len(n500) < 700:
        raise RuntimeError("Nifty 500 series missing in the store")
    cl, vo = {}, {}
    for sym, raw in res.items():
        c, v = _adjusted(raw)
        if c is not None:
            cl[sym + ".NS"], vo[sym + ".NS"] = c, v
    if len(cl) < 0.9 * len(syms):
        raise RuntimeError(f"store has bars for only {len(cl)} of {len(syms)} symbols")
    return n500.dropna(), pd.DataFrame(cl).sort_index(), pd.DataFrame(vo).sort_index(), {"last_day": st.get("last_day"), "symbols_in_store": st.get("symbols")}


def nse_had_session(day):
    """True / False / None (unknown). Same rule as the Aria desk's calendar: a day is a session only if NSE's file for that day
    carries that day's own date (NSE's older archive serves the previous session's file for a non-session date)."""
    url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{day:%d%m%Y}.csv"
    for k in range(3):
        try:
            head = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=40).read(4000).decode("latin-1")
            for line in head.splitlines()[1:6]:
                cells = line.split(",")
                if len(cells) > 2:
                    return cells[1].strip() == f"{day:%d-%m-%Y}"
            return None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            time.sleep(3 * (k + 1))
        except Exception:
            time.sleep(3 * (k + 1))
    return None


def unexplained_jumps(stk_df, events_note=None):
    """Adjusted series should be smooth where splits are known. A leftover one-day jump that looks like a split (or a
    reverse split) means an event is missing: that stock is held out of this run and listed for review."""
    bad = {}
    r = stk_df / stk_df.shift(1)
    for t in stk_df.columns:
        x = r[t].dropna().tail(270)   # only jumps that can still affect today's indicators (older ones are history)
        for d, v in x[(x < 0.62) | (x > 1.75)].items():
            f = clean_fraction(v, 0.06) if v < 1 else (lambda g: (1 / g) if g else None)(clean_fraction(1 / v, 0.06))
            if f:
                bad.setdefault(t[:-3], []).append((d.date().isoformat(), round(float(v), 4)))
    return bad


def yahoo_crosscheck(stk_df, tickers_sample=None):
    """Independent check of the latest raw close against Yahoo. Non-blocking unless many stocks disagree."""
    try:
        cols = list(stk_df.columns)
        out, bad = 0, []
        for i in range(0, len(cols), 120):
            chunk = cols[i:i + 120]
            df = yf.download(chunk, period="7d", interval="1d", progress=False, auto_adjust=False)
            cdf = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": chunk[0]})
            cdf.index = pd.to_datetime(cdf.index).tz_localize(None)
            for t in chunk:
                if t not in cdf.columns:
                    continue
                a = stk_df[t].dropna()
                y = cdf[t].dropna()
                common = a.index.intersection(y.index)
                if len(common) == 0:
                    continue
                d = common[-1]
                out += 1
                if abs(a[d] / y[d] - 1) > 0.005:
                    bad.append((t[:-3], d.date().isoformat(), round(float(a[d]), 2), round(float(y[d]), 2)))
        return {"checked": out, "mismatch": bad}
    except Exception as e:
        return {"checked": 0, "mismatch": [], "error": str(e)[:120]}


MIN_LIQUID_TURNOVER = 1e7   # Rs 1 crore median daily turnover over 60 sessions (the backtest's universe rule)
MIN_SECTOR_SIZE = 15        # groups smaller than this are shown but never ranked, led or cut


def evaluate_snapshot(n500_c: pd.Series, stk_df: pd.DataFrame, vol_df: pd.DataFrame, universe: list[dict], offset_bars: int = 0):
    # The snapshot date comes from the Nifty 500 calendar and every stock is cut at that DATE (not at a row position).
    asof = n500_c.index[-1 - offset_bars]
    n500_slice = n500_c.loc[:asof]
    n500_last = round(float(n500_slice.iloc[-1]), 2)
    n500_sma200 = round(float(n500_slice.rolling(200).mean().iloc[-1]), 2)
    snap_date = str(n500_slice.index[-1].date())
    market_mode = "INVEST" if n500_last > n500_sma200 else "CASH"

    meta_by_ticker = {u["yf_ticker"]: u for u in universe}
    rows = []
    sector_buckets: dict[str, list[tuple[float, float]]] = {}

    for u in universe:
        tick = u["yf_ticker"]
        if tick not in stk_df.columns:
            continue
        s = stk_df[tick].loc[:asof].dropna()
        if len(s) < 260:
            continue
        if (asof - s.index[-1]).days > 10:   # halted / delisted / no recent trade at this date
            continue
        if tick in vol_df.columns:
            tv = (stk_df[tick] * vol_df[tick]).loc[:asof].dropna().tail(60)
        else:
            tv = pd.Series(dtype=float)
        if len(tv) < 40 or float(tv.median()) < MIN_LIQUID_TURNOVER:
            continue

        meta = meta_by_ticker[tick]
        c = float(s.iloc[-1])
        sma50 = float(s.rolling(50).mean().iloc[-1])
        sma150 = float(s.rolling(150).mean().iloc[-1])
        sma200 = float(s.rolling(200).mean().iloc[-1])
        sma200_20d = float(s.rolling(200).mean().iloc[-21])
        hi52 = float(s.rolling(252).max().iloc[-1])
        lo52 = float(s.rolling(252).min().iloc[-1])
        ret12 = safe_pct(c, float(s.iloc[-252]))
        ret6 = safe_pct(c, float(s.iloc[-126]))
        vol60 = float(s.pct_change().rolling(60).std().iloc[-1] * (252 ** 0.5) * 100.0)
        vol60 = round(vol60, 2) if pd.notna(vol60) and vol60 > 0 else 0.0
        smooth_score = round(ret12 / vol60, 2) if vol60 > 0 else 0.0
        pct_from_hi = round(((c / hi52) - 1.0) * 100.0, 1)
        pct_above_lo = round(((c / lo52) - 1.0) * 100.0, 1)

        if meta["sector"] != "Other":
            sector_buckets.setdefault(meta["sector"], []).append((ret6, ret12))

        r1 = bool(c > sma150 and c > sma200)
        r2 = bool(sma150 > sma200)
        r3 = bool(sma200 > sma200_20d)
        r4 = bool(sma50 > sma150 and sma50 > sma200)
        r5 = bool(c > sma50)
        r6 = bool(c >= 1.30 * lo52)
        r7 = bool(c >= 0.75 * hi52)
        rules_7 = [r1, r2, r3, r4, r5, r6, r7]

        rows.append({
            "symbol": meta["symbol"],
            "company": meta["company"],
            "industry": meta["industry"],
            "sector": meta["sector"],
            "close": round(c, 2),
            "sma50": round(sma50, 2),
            "sma150": round(sma150, 2),
            "sma200": round(sma200, 2),
            "sma200_20d": round(sma200_20d, 2),
            "hi52": round(hi52, 2),
            "lo52": round(lo52, 2),
            "pct_from_52w_hi": pct_from_hi,
            "pct_above_52w_lo": pct_above_lo,
            "ret_6m": ret6,
            "ret_12m": ret12,
            "vol_60d": vol60,
            "smooth_score": smooth_score,
            "rules_7_count": sum(rules_7),
            "rules_7_ok": all(rules_7),
            "rules": {
                "r1": r1, "r2": r2, "r3": r3, "r4": r4,
                "r5": r5, "r6": r6, "r7": r7, "r8": False,
            },
        })

    sector_rows = []
    for sec, vals in sector_buckets.items():
        med_6m = round(statistics.median(v[0] for v in vals), 2)
        med_12m = round(statistics.median(v[1] for v in vals), 2)
        score = round(0.5 * med_6m + 0.5 * med_12m, 2)
        sector_rows.append({"name": sec, "sector": sec, "count": len(vals), "ret_6m": med_6m, "ret_12m": med_12m,
                            "score": score, "eligible": len(vals) >= MIN_SECTOR_SIZE})
    big = sorted((r for r in sector_rows if r["eligible"]), key=lambda x: x["score"], reverse=True)
    small = sorted((r for r in sector_rows if not r["eligible"]), key=lambda x: x["score"], reverse=True)
    sector_rows = big + small
    for idx, r in enumerate(sector_rows):
        r["rank"] = idx + 1
        if not r["eligible"]:
            r["status"] = "KEEP"
        else:
            r["status"] = "CUT" if idx >= max(0, len(big) - 3) else ("LEADER" if idx < 4 else "KEEP")
    cut_sectors = {r["sector"] for r in sector_rows if r["status"] == "CUT"}

    ret12_vals = sorted(r["ret_12m"] for r in rows)
    n = max(1, len(ret12_vals) - 1)
    for r in rows:
        rank_idx = ret12_vals.index(r["ret_12m"])
        r["rs_pct"] = round((rank_idx / n) * 100.0, 1)
        r["r8_rs"] = bool(r["rs_pct"] >= 70.0)
        r["rules"]["r8"] = r["r8_rs"]
        r["pass_8_rules"] = bool(r["rules_7_ok"] and r["r8_rs"])
        r["score_of_8"] = r["rules_7_count"] + (1 if r["r8_rs"] else 0)
        r["sector_cut"] = r["sector"] in cut_sectors
        r["dma150_exit_broken"] = bool(r["close"] < r["sma150"])
        r["qualifies"] = bool(r["pass_8_rules"] and not r["sector_cut"] and not r["dma150_exit_broken"])

    rows.sort(key=lambda x: (x["qualifies"], x["pass_8_rules"], x["score_of_8"], x["smooth_score"]), reverse=True)
    return {
        "market_date": snap_date,
        "universe_size": len(universe),
        "market_weather": {
            "index": "NIFTY 500",
            "close": n500_last,
            "sma200": n500_sma200,
            "diff_pct": safe_pct(n500_last, n500_sma200),
            "mode": market_mode,
        },
        "sectors": sector_rows,
        "stocks": rows,
        "summary": {
            "total_scanned": len(rows),
            "pass_8_all": sum(1 for r in rows if r["pass_8_rules"]),
            "pass_8_leading_sectors": sum(1 for r in rows if r["qualifies"]),
            "near_breakout_7": sum(1 for r in rows if r["score_of_8"] == 7 and not r["sector_cut"]),
            "exit_broken_150": sum(1 for r in rows if r["dma150_exit_broken"]),
            "cut_sectors": sorted(cut_sectors),
        },
    }


def apply_chronological_tracking(snapshots: dict, sorted_dates: list[str], adj):
    """Walk oldest->newest so EVERY week tracks entry_date, entry_price, ret_since_entry, days_in_list, streak_6m, and exit_price."""
    tracker: dict[str, dict] = {}
    prev_snap = None

    for idx, dt in enumerate(sorted_dates):
        snap = snapshots[dt]
        dt_obj = datetime.strptime(dt, "%Y-%m-%d").date()
        # In a CASH week (Nifty 500 below its 200-DMA) the tested system holds cash: nothing is held, nothing is "new".
        cash = (snap.get("market_weather") or {}).get("mode") == "CASH"
        prev_cash = bool(prev_snap and (prev_snap.get("market_weather") or {}).get("mode") == "CASH")
        prev_qual = {r["symbol"]: r for r in prev_snap["stocks"] if r["qualifies"] and not prev_cash} if prev_snap else {}
        cur_by_sym = {r["symbol"]: r for r in snap["stocks"]}
        added = []
        removed = []
        held_count = 0

        for r in snap["stocks"]:
            sym = r["symbol"]
            if r["qualifies"] and not cash:
                cur_px = adj(sym, dt) or r["close"]
                if sym not in tracker:
                    tracker[sym] = {"entry_date": dt, "entry_price": cur_px}
                ent = tracker[sym]
                days_in = (dt_obj - datetime.strptime(ent["entry_date"], "%Y-%m-%d").date()).days
                r["entry_date"] = ent["entry_date"]
                r["entry_price"] = ent["entry_price"]
                r["days_in_list"] = days_in
                r["streak_6m"] = bool(days_in >= 180)
                r["ret_since_entry"] = safe_pct(cur_px, ent["entry_price"])
                r["exit_price"] = None
                r["exit_reason"] = ""
                if prev_snap is None:
                    r["week_status"] = "HELD"
                    held_count += 1
                elif sym not in prev_qual:
                    r["week_status"] = "ADDED"
                    added.append({
                        "symbol": sym, "company": r["company"], "sector": r["sector"],
                        "entry_date": ent["entry_date"], "entry_price": ent["entry_price"],
                        "days_in_list": days_in, "streak_6m": r["streak_6m"],
                        "close": r["close"], "sma150": r["sma150"], "smooth_score": r["smooth_score"], "rs_pct": r["rs_pct"],
                    })
                else:
                    r["week_status"] = "HELD"
                    held_count += 1
            elif sym in prev_qual:
                cur_px = adj(sym, dt) or r["close"]
                ent = tracker.pop(sym, {"entry_date": prev_snap["market_date"] if prev_snap else dt,
                                        "entry_price": adj(sym, prev_snap["market_date"] if prev_snap else dt) or prev_qual[sym]["close"]})
                days_in = (dt_obj - datetime.strptime(ent["entry_date"], "%Y-%m-%d").date()).days
                reason = ("Market filter: CASH" if cash else "Broke 150-DMA" if r["dma150_exit_broken"] else ("Sector Cut" if r["sector_cut"] else f"Dropped <8/8 ({r['score_of_8']}/8)"))
                ret_trade = safe_pct(cur_px, ent["entry_price"])
                r["entry_date"] = ent["entry_date"]
                r["entry_price"] = ent["entry_price"]
                r["days_in_list"] = days_in
                r["streak_6m"] = bool(days_in >= 180)
                r["exit_price"] = cur_px
                r["ret_since_entry"] = ret_trade
                r["week_status"] = "REMOVED"
                r["exit_reason"] = reason
                removed.append({
                    "symbol": sym, "company": r["company"], "sector": r["sector"], "reason": reason,
                    "entry_date": ent["entry_date"], "entry_price": ent["entry_price"],
                    "days_in_list": days_in, "streak_6m": r["streak_6m"],
                    "exit_price": cur_px, "ret_since_entry": ret_trade,
                    "close": r["close"], "sma150": r["sma150"],
                })
            else:
                r["entry_date"] = None
                r["entry_price"] = None
                r["days_in_list"] = 0
                r["streak_6m"] = False
                r["exit_price"] = None
                r["ret_since_entry"] = None
                r["week_status"] = ""
                r["exit_reason"] = ""

        for sym, pr in prev_qual.items():
            if sym not in cur_by_sym:
                tracker.pop(sym, None)

        snap["summary"]["streak_6m_count"] = sum(1 for r in snap["stocks"] if r["qualifies"] and r.get("streak_6m"))
        snap["diff"] = {
            "prev_date": prev_snap["market_date"] if prev_snap else None,
            "added": added,
            "removed": removed,
            "held_count": held_count,
        }
        prev_snap = snap


def fail(msg: str):
    print("ABORT: " + msg)
    sys.exit(1)


def main():
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    now_ist = now.strftime("%d %b %Y, %I:%M %p IST")

    universe = load_universe()
    tickers = [u["yf_ticker"] for u in universe]
    data_source, store_info, review_hold, ycheck = "store", {}, {}, {}
    try:
        n500_c, stk_df, vol_df, store_info = load_store(universe)
    except Exception as e:
        print("NSE store unavailable, falling back to Yahoo:", e)
        data_source = "yahoo-fallback"
        n500 = None
        for k in range(3):
            try:
                n500 = yf.download("^CRSLDX", period="3y", interval="1d", progress=False, auto_adjust=True)
                if not n500.empty:
                    break
            except Exception as e2:
                print(f"index download error ({e2})")
            time.sleep(5 * (k + 1))
        if n500 is None or n500.empty:
            fail("Nifty 500 index download failed (store and Yahoo both unavailable)")
        n500_c = n500["Close"].iloc[:, 0].dropna() if isinstance(n500.columns, pd.MultiIndex) else n500["Close"].dropna()
        stk_df, vol_df = download_batched(tickers, batch_size=120)
        if stk_df.empty:
            fail("no stock data downloaded")
    n500_c.index = pd.to_datetime(n500_c.index).tz_localize(None)
    stk_df.index = pd.to_datetime(stk_df.index).tz_localize(None)
    vol_df.index = pd.to_datetime(vol_df.index).tz_localize(None)

    # held-out stocks: an unexplained split-like jump means a corporate action is missing from the data
    review_hold = unexplained_jumps(stk_df)
    if review_hold:
        print("HELD OUT for review (unexplained jumps):", {k: v for k, v in list(review_hold.items())[:20]})
        keep = [c for c in stk_df.columns if c[:-3] not in review_hold]
        stk_df, vol_df = stk_df[keep], vol_df[[c for c in vol_df.columns if c in keep]]
    if len(review_hold) > 0.03 * len(tickers):
        fail(f"{len(review_hold)} stocks show unexplained jumps; refusing to publish")
    ycheck = yahoo_crosscheck(stk_df)
    print("Yahoo cross-check:", ycheck["checked"], "compared,", len(ycheck["mismatch"]), "differ", ycheck.get("error", ""))
    if ycheck["checked"] > 200 and len(ycheck["mismatch"]) > 0.03 * ycheck["checked"]:
        fail(f"{len(ycheck['mismatch'])} of {ycheck['checked']} latest closes disagree with Yahoo; refusing to publish")

    # ---- sanity checks: stop BEFORE writing anything if the download looks wrong
    if len(n500_c) < 700:
        fail(f"Nifty 500 history too short ({len(n500_c)} bars)")
    last_bar = n500_c.index[-1]
    if (now.replace(tzinfo=None) - last_bar.to_pydatetime()).days > 4:
        fail(f"Nifty 500 latest bar is {last_bar.date()}, more than 4 days old")
    # On a run after the week has ended, that week's Friday must be in the data, unless NSE had no session that day.
    freshness_note = ""
    p = pd.Timestamp(now.date()).to_period("W-FRI")
    if now.weekday() == 5 or now.weekday() == 6 or (now.weekday() == 4 and now.hour >= 19):
        fri = p.end_time.date() if now.weekday() <= 4 else (p - 1).end_time.date()
        if last_bar.date() < fri:
            had = nse_had_session(fri)
            if had is True:
                fail(f"NSE traded on {fri} but the newest bar in the data is {last_bar.date()}; the data is not up to date yet")
            if had is None:
                freshness_note = f"could not confirm whether NSE traded on {fri}; newest bar is {last_bar.date()}"
                print("WARNING:", freshness_note)
    long_enough = sum(1 for t in tickers if t in stk_df.columns and stk_df[t].dropna().shape[0] >= 260)
    fresh = sum(1 for t in tickers if t in stk_df.columns and not stk_df[t].dropna().empty
                and (last_bar - stk_df[t].dropna().index[-1]).days <= 3)
    if long_enough < 0.95 * len(tickers):
        fail(f"only {long_enough} of {len(tickers)} stocks have enough history")
    if fresh < 0.95 * len(tickers):
        fail(f"only {fresh} of {len(tickers)} stocks have a recent price")
    prev_idx = HIST_DIR / "index.json"
    if prev_idx.exists():
        try:
            prev_latest = json.loads(prev_idx.read_text(encoding="utf-8")).get("latest")
            if prev_latest and str(last_bar.date()) < prev_latest:
                fail(f"new data ({last_bar.date()}) is older than the published snapshot ({prev_latest})")
        except json.JSONDecodeError:
            pass

    # Weekly checkpoints = the last trading day of each COMPLETE week (Friday, or the last session before a holiday).
    # A run in the middle of a week never creates a half-week snapshot.
    existing_dates = {p.stem for p in HIST_DIR.glob("*.json") if p.stem != "index"}
    rebuild = "--rebuild" in sys.argv
    idx = n500_c.index
    per = idx.to_period("W-FRI")
    week_end_pos = list(pd.Series(range(len(idx)), index=idx).groupby(per).max().values)
    now_period = pd.Timestamp(now.date()).to_period("W-FRI")
    week_complete = (last_bar.to_period("W-FRI") < now_period) or (now.weekday() == 4 and now.hour >= 19)
    if not week_complete:
        week_end_pos = week_end_pos[:-1]
    week_end_pos = week_end_pos[-53:]
    if not rebuild and len(existing_dates) >= 50:
        week_end_pos = week_end_pos[-1:]
    offsets_to_build = [len(idx) - 1 - p for p in week_end_pos]

    snapshots = {}
    for off in offsets_to_build:
        snap = evaluate_snapshot(n500_c, stk_df, vol_df, universe, offset_bars=off)
        snap["updated_at_ist"] = now_ist if off == offsets_to_build[-1] else f"Weekly Close ({snap['market_date']})"
        snapshots[snap["market_date"]] = snap
    new_periods = {pd.Timestamp(d).to_period("W-FRI"): d for d in snapshots}

    # Keep (never delete) snapshots from an older method, or from the same week with a different last day.
    keep_dir = HIST_DIR / "_superseded"
    for p in list(HIST_DIR.glob("*.json")):
        if p.stem == "index" or p.stem in snapshots:
            continue
        same_week = pd.Timestamp(p.stem).to_period("W-FRI") in new_periods
        if rebuild or same_week:
            keep_dir.mkdir(exist_ok=True)
            p.replace(keep_dir / p.name)
    for p in HIST_DIR.glob("*.json"):
        if p.stem == "index" or p.stem in snapshots:
            continue
        try:
            snapshots[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    sorted_dates = sorted(snapshots.keys())
    series = {u["symbol"]: stk_df[u["yf_ticker"]].dropna() for u in universe if u["yf_ticker"] in stk_df.columns}

    def adj(sym, d):
        ser = series.get(sym)
        if ser is None or ser.empty:
            return None
        i = ser.index.searchsorted(pd.Timestamp(d), side="right") - 1
        return round(float(ser.iloc[i]), 2) if i >= 0 else None

    apply_chronological_tracking(snapshots, sorted_dates, adj)

    for dt in sorted_dates:
        (HIST_DIR / f"{dt}.json").write_text(json.dumps(snapshots[dt], separators=(",", ":")), encoding="utf-8")

    # Expose only the recent 9 weeks in the dropdown (all 53 weeks remain in history/ for accurate 12M entry tracking)
    all_diff_dates = list(reversed(sorted_dates[1:] if len(sorted_dates) > 1 else sorted_dates))
    visible_dates = all_diff_dates[:9]
    (HIST_DIR / "index.json").write_text(json.dumps({"dates": visible_dates, "latest": visible_dates[0]}, indent=2), encoding="utf-8")

    latest = snapshots[visible_dates[0]]
    (OUT_DIR / "screener.json").write_text(json.dumps(latest, separators=(",", ":")), encoding="utf-8")

    with (OUT_DIR / "screener.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Symbol", "Company", "Sector", "SectorStatus", "WeekStatus", "EntryDate", "EntryPrice", "ExitPrice", "RetSinceEntry%", "ExitReason", "Close", "SMA50", "SMA150", "SMA200", "From52WHi%", "Above52WLo%", "Ret6M%", "Ret12M%", "Vol60D%", "SmoothScore", "RSPct", "ScoreOf8", "Pass8Rules"])
        for r in latest["stocks"]:
            w.writerow([
                r["symbol"], r["company"], r["sector"], "CUT" if r["sector_cut"] else "KEEP",
                r.get("week_status", ""), r.get("entry_date") or "", r.get("entry_price") or "",
                r.get("exit_price") or "", r.get("ret_since_entry") if r.get("ret_since_entry") is not None else "",
                r.get("exit_reason", ""),
                r["close"], r["sma50"], r["sma150"], r["sma200"],
                r["pct_from_52w_hi"], r["pct_above_52w_lo"], r["ret_6m"], r["ret_12m"], r.get("vol_60d", 0), r.get("smooth_score", 0), r["rs_pct"],
                r["score_of_8"], "PASS" if r["pass_8_rules"] else "FAIL"
            ])

    issues = []
    if data_source != "store":
        issues.append("This week's screener used backup data from Yahoo because the NSE price store was unavailable.")
    if review_hold:
        issues.append(f"{len(review_hold)} stock(s) are held out of this list because of an unexplained price jump (a possible missed split): " + ", ".join(sorted(review_hold)[:12]) + ".")
    if freshness_note:
        issues.append(freshness_note[0].upper() + freshness_note[1:] + ".")
    if ycheck.get("error"):
        issues.append("The independent price check against Yahoo could not run (" + ycheck["error"] + ").")
    if ycheck.get("mismatch"):
        issues.append(f"{len(ycheck['mismatch'])} latest close(s) differ from Yahoo's: " + ", ".join(m[0] for m in ycheck["mismatch"][:10]) + ". The NSE value is shown.")
    (OUT_DIR / "status.json").write_text(json.dumps({
        "updated_utc": datetime.now(timezone.utc).isoformat(), "issues": issues,
        "ok": True, "updated_at_ist": now_ist, "market_date": latest["market_date"],
        "universe": len(universe), "scanned": latest["summary"]["total_scanned"],
        "qualifying": latest["summary"]["pass_8_leading_sectors"],
        "data_source": data_source, "freshness_note": freshness_note, "held_out_for_review": review_hold,
        "yahoo_crosscheck": {"checked": ycheck.get("checked", 0), "differ": ycheck.get("mismatch", [])[:30]},
    }, indent=2), encoding="utf-8")

    for dt in visible_dates:
        s = snapshots[dt]
        d = s["diff"]
        print(f"  {dt}: Leaders={s['summary']['pass_8_leading_sectors']} | +{len(d['added'])} Added | -{len(d['removed'])} Removed (vs {d['prev_date']})")


if __name__ == "__main__":
    main()
