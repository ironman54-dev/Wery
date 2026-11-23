import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone, timedelta
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
        prev_qual = {r["symbol"]: r for r in prev_snap["stocks"] if r["qualifies"]} if prev_snap else {}
        cur_by_sym = {r["symbol"]: r for r in snap["stocks"]}
        added = []
        removed = []
        held_count = 0

        for r in snap["stocks"]:
            sym = r["symbol"]
            if r["qualifies"]:
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
                reason = "Broke 150-DMA" if r["dma150_exit_broken"] else ("Sector Cut" if r["sector_cut"] else f"Dropped <8/8 ({r['score_of_8']}/8)")
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

    n500 = None
    for k in range(3):
        try:
            n500 = yf.download("^CRSLDX", period="3y", interval="1d", progress=False, auto_adjust=True)
            if not n500.empty:
                break
        except Exception as e:
            print(f"index download error ({e})")
        time.sleep(5 * (k + 1))
    if n500 is None or n500.empty:
        fail("Nifty 500 index download failed")
    if isinstance(n500.columns, pd.MultiIndex):
        n500_c = n500["Close"].iloc[:, 0].dropna()
    else:
        n500_c = n500["Close"].dropna()
    n500_c.index = pd.to_datetime(n500_c.index).tz_localize(None)

    universe = load_universe()
    tickers = [u["yf_ticker"] for u in universe]
    stk_df, vol_df = download_batched(tickers, batch_size=120)
    if stk_df.empty:
        fail("no stock data downloaded")
    stk_df.index = pd.to_datetime(stk_df.index).tz_localize(None)
    vol_df.index = pd.to_datetime(vol_df.index).tz_localize(None)

    # ---- sanity checks: stop BEFORE writing anything if the download looks wrong
    if len(n500_c) < 700:
        fail(f"Nifty 500 history too short ({len(n500_c)} bars)")
    last_bar = n500_c.index[-1]
    if (now.replace(tzinfo=None) - last_bar.to_pydatetime()).days > 7:
        fail(f"Nifty 500 latest bar is {last_bar.date()}, more than 7 days old")
    long_enough = sum(1 for t in tickers if t in stk_df.columns and stk_df[t].dropna().shape[0] >= 260)
    fresh = sum(1 for t in tickers if t in stk_df.columns and not stk_df[t].dropna().empty
                and (last_bar - stk_df[t].dropna().index[-1]).days <= 5)
    if long_enough < 0.85 * len(tickers):
        fail(f"only {long_enough} of {len(tickers)} stocks have enough history")
    if fresh < 0.85 * len(tickers):
        fail(f"only {fresh} of {len(tickers)} stocks have a recent price")
    prev_idx = HIST_DIR / "index.json"
    if prev_idx.exists():
        try:
            prev_latest = json.loads(prev_idx.read_text(encoding="utf-8")).get("latest")
            if prev_latest and str(last_bar.date()) < prev_latest:
                fail(f"new data ({last_bar.date()}) is older than the published snapshot ({prev_latest})")
        except json.JSONDecodeError:
            pass

    # Build 53 weekly checkpoints (~12 months) so entry_date & 6M+ streak are accurate, while keeping dropdown to recent 9 weeks!
    existing_dates = {p.stem for p in HIST_DIR.glob("*.json") if p.stem != "index"}
    rebuild = "--rebuild" in sys.argv
    offsets_to_build = list(range(260, -1, -5)) if (rebuild or len(existing_dates) < 50) else [0]

    snapshots = {}
    for off in offsets_to_build:
        snap = evaluate_snapshot(n500_c, stk_df, vol_df, universe, offset_bars=off)
        snap["updated_at_ist"] = now_ist if off == 0 else f"Weekly Close ({snap['market_date']})"
        snapshots[snap["market_date"]] = snap

    if rebuild:   # keep (do not delete) snapshots made by an older method that no new snapshot replaces
        old_dir = HIST_DIR / "_old_method"
        old_dir.mkdir(exist_ok=True)
        for p in list(HIST_DIR.glob("*.json")):
            if p.stem != "index" and p.stem not in snapshots:
                p.replace(old_dir / p.name)
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

    (OUT_DIR / "status.json").write_text(json.dumps({
        "ok": True, "updated_at_ist": now_ist, "market_date": latest["market_date"],
        "universe": len(universe), "scanned": latest["summary"]["total_scanned"],
        "qualifying": latest["summary"]["pass_8_leading_sectors"],
    }, indent=2), encoding="utf-8")

    for dt in visible_dates:
        s = snapshots[dt]
        d = s["diff"]
        print(f"  {dt}: Leaders={s['summary']['pass_8_leading_sectors']} | +{len(d['added'])} Added | -{len(d['removed'])} Removed (vs {d['prev_date']})")


if __name__ == "__main__":
    main()
