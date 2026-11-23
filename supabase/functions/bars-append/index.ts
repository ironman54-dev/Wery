import { createClient } from "npm:@supabase/supabase-js@2";

// Adds the newest NSE end-of-day bars (stocks + Nifty 500) to cc_bars. Reads only NSE's public archive files and Yahoo's public
// split list; writes with the project's own service role. A split/bonus is applied automatically only when TWO independent signals agree:
// (1) the raw price gap matches a clean fraction, (2) Yahoo lists a split/bonus on that date with the same factor.
// NSE's PREV_CLOSE in the bhavcopy is NOT adjusted, so it cannot be used. Anything else that looks like a split goes to cc_event_review,
// and the screener holds that stock out of its run until the event is checked against NSE's own corporate actions.
// Every calendar day is tried (weekends too): NSE sometimes holds special sessions, such as Muhurat trading on a Sunday.
// A file only counts when the date inside it is the day it was asked for (NSE's older archive serves the previous session's file for a non-session day).
// Every run writes a health record (cc_health). Anything unexpected becomes an issue that the website shows as a red pill.
const DAY = 86400000;
const OK = new Set(["EQ", "BE", "BZ", "SM", "ST"]);
const RANK: Record<string, number> = { EQ: 0, BE: 1, BZ: 2, SM: 3, ST: 4 };
const MON: Record<string, number> = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
const CLEAN: number[] = (() => {
  const s = new Set<number>([1 / 20, 1 / 25, 1 / 50, 1 / 100]);
  for (let b = 2; b <= 12; b++) for (let a = 1; a < b; a++) s.add(a / b);
  return [...s];
})();

function parseDate(s: string): number {
  const [d, m, y] = s.trim().split("-");
  return Math.round(Date.UTC(+y, MON[m], +d) / DAY);
}
function cleanFraction(r: number, tol = 0.12): number | null {
  let best = CLEAN[0];
  for (const f of CLEAN) if (Math.abs(r / f - 1) < Math.abs(r / best - 1)) best = f;
  return Math.abs(r / best - 1) <= tol ? best : null;
}
function candidateFactor(r: number): number | null {
  if (r < 0.78) return cleanFraction(r);
  if (r > 1.7) { const g = cleanFraction(1 / r); return g ? 1 / g : null; }
  return null;
}
async function yahooSplits(sym: string): Promise<Record<string, number> | null> {
  try {
    const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}.NS?range=1mo&interval=1d&events=split`, { headers: { "User-Agent": "Mozilla/5.0" } });
    if (!r.ok) return null;
    const j = await r.json();
    const sp = j?.chart?.result?.[0]?.events?.splits ?? {};
    const out: Record<string, number> = {};
    for (const v of Object.values(sp) as { date: number; numerator: number; denominator: number }[]) {
      if (v.numerator && v.denominator) out[new Date(v.date * 1000).toISOString().slice(0, 10)] = v.denominator / v.numerator;
    }
    return out;
  } catch (_) {
    return null;
  }
}
type Got = { status: number; text: string } | null;
async function get(url: string): Promise<Got> {
  for (let k = 0; k < 3; k++) {
    try {
      const r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } });
      if (r.status === 404) return { status: 404, text: "" };
      if (r.ok) return { status: r.status, text: await r.text() };
      if (k === 2) return { status: r.status, text: "" };
    } catch (_) {
      if (k === 2) return null;
    }
    await new Promise((res) => setTimeout(res, 1500 * (k + 1)));
  }
  return null;
}
const ymd = (dayNum: number) => {
  const dt = new Date(dayNum * DAY);
  return `${String(dt.getUTCDate()).padStart(2, "0")}${String(dt.getUTCMonth() + 1).padStart(2, "0")}${dt.getUTCFullYear()}`;
};
const iso = (dayNum: number) => new Date(dayNum * DAY).toISOString().slice(0, 10);

type Row = { s: string; y: number; d: number[]; o: number[]; h: number[]; l: number[]; c: number[]; v: number[] };

Deno.serve(async () => {
  const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const issues: string[] = [];
  const finish = async (body: Record<string, unknown>, status = 200) => {
    await sb.rpc("cc_health_put", { p_token: null, p_source: "bars-append", p_ok: issues.length === 0, p_issues: issues });
    return new Response(JSON.stringify({ ...body, issues }), { status, headers: { "Content-Type": "application/json" } });
  };
  try {
    const st = await sb.rpc("cc_bars_status");
    if (st.error || !st.data) { issues.push("The price store could not be read."); return await finish({ ok: false }, 500); }
    const last: number = st.data.last_day;
    const lastClose: Record<string, number> = { ...st.data.last_close }; // paise
    const symbols = new Set(Object.keys(lastClose));
    const nSyms = symbols.size - 1; // without NIFTY500
    const ist = new Date(Date.now() + 5.5 * 3600 * 1000);
    const today = Math.floor(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()) / DAY);
    if (last === 0 || today - last > 45) {
      issues.push("The stored prices are missing or more than 45 days old; a full reload is needed.");
      return await finish({ ok: false, last, today }, 409);
    }

    const bars: Record<string, Row> = {};
    const add = (sym: string, dn: number, o: number, h: number, l: number, c: number, v: number) => {
      const y = new Date(dn * DAY).getUTCFullYear();
      const b = (bars[sym + "|" + y] ??= { s: sym, y, d: [], o: [], h: [], l: [], c: [], v: [] });
      b.d.push(dn); b.o.push(o); b.h.push(h); b.l.push(l); b.c.push(c); b.v.push(v);
    };
    const events: { s: string; d: string; f: number }[] = [];
    const review: { s: string; d: string; r: number; n: string }[] = [];
    const yCache: Record<string, Record<string, number> | null> = {};
    let yahooDown = 0;
    let days = 0, newest = last;
    for (let day = last + 1; day <= today; day++) {
      const dstr = iso(day);
      const bh = await get(`https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_${ymd(day)}.csv`);
      const ixf = await get(`https://nsearchives.nseindia.com/content/indices/ind_close_all_${ymd(day)}.csv`);
      if (bh === null || ixf === null || (bh.status !== 200 && bh.status !== 404) || (ixf.status !== 200 && ixf.status !== 404)) {
        issues.push(`NSE's archive could not be reached for ${dstr} (status ${bh ? bh.status : "no answer"}/${ixf ? ixf.status : "no answer"}); that day's prices are not downloaded.`);
        continue;
      }
      if (bh.status === 404 && ixf.status === 404) continue; // weekend or holiday: NSE published nothing
      if (bh.status === 404) { issues.push(`NSE's index file for ${dstr} exists but its stock price file does not (not downloaded yet?).`); }
      if (bh.status === 200) {
        const lines = bh.text.split(/\r?\n/);
        const head = lines[0].split(",").map((x) => x.trim().toUpperCase());
        const ix = (n: string) => head.indexOf(n);
        const iS = ix("SYMBOL"), iSe = ix("SERIES"), iD = ix("DATE1"), iO = ix("OPEN_PRICE"), iH = ix("HIGH_PRICE"), iL = ix("LOW_PRICE"), iC = ix("CLOSE_PRICE"), iV = ix("TTL_TRD_QNTY");
        if ([iS, iSe, iD, iO, iH, iL, iC, iV].some((i) => i < 0)) {
          issues.push(`NSE's price file for ${dstr} has a different layout from before; it was skipped.`);
        } else {
          const best: Record<string, { r: number; row: string[] }> = {};
          for (let i = 1; i < lines.length; i++) {
            const p = lines[i].split(",");
            if (p.length < head.length) continue;
            const sym = p[iS].trim(), ser = p[iSe].trim();
            if (!symbols.has(sym) || sym === "NIFTY500" || !OK.has(ser)) continue;
            if (!best[sym] || RANK[ser] < best[sym].r) best[sym] = { r: RANK[ser], row: p };
          }
          let used = 0, copy = 0;
          for (const [sym, { row }] of Object.entries(best)) {
            const dn = parseDate(row[iD]);
            if (dn !== day) { copy++; continue; } // the file is not for this day (a copy of an earlier session)
            const o = Math.round(parseFloat(row[iO]) * 100), h = Math.round(parseFloat(row[iH]) * 100), l = Math.round(parseFloat(row[iL]) * 100), c = Math.round(parseFloat(row[iC]) * 100);
            const v = parseInt(row[iV]) || 0;
            if (![o, h, l, c].every((x) => Number.isFinite(x) && x > 0) || h < l) continue;
            const prev = lastClose[sym];
            if (prev) {
              const f = candidateFactor(c / prev);
              if (f) {
                if (!(sym in yCache)) yCache[sym] = await yahooSplits(sym);
                const ys = yCache[sym], yf = ys ? ys[dstr] : undefined;
                if (ys === null) yahooDown++;
                if (yf && Math.abs(yf / f - 1) <= 0.01) events.push({ s: sym, d: dstr, f: Math.round(yf * 1e6) / 1e6 });
                else review.push({ s: sym, d: dstr, r: Math.round((c / prev) * 1e4) / 1e4, n: ys === null ? "price gap looks like a split; Yahoo unreachable" : "price gap looks like a split; no matching Yahoo split" });
              }
            }
            lastClose[sym] = c;
            add(sym, dn, o, h, l, c, v);
            used++;
            if (dn > newest) newest = dn;
          }
          if (copy > 0 && used === 0) continue; // a copy of an earlier file, not a session
          if (used < 0.85 * nSyms) issues.push(`NSE's price file for ${dstr} had only ${used} of ${nSyms} stocks.`);
          if (used > 0) days++;
        }
      }
      if (ixf.status === 200) {
        let found = false;
        for (const ln of ixf.text.split(/\r?\n/)) {
          const p = ln.split(",");
          if (p[0] && p[0].trim().toLowerCase() === "nifty 500") {
            const parts = p[1].trim().split("-");
            const key = ymd(day);
            if (parts.length !== 3 || !(parts[0] + parts[1] + parts[2] === key || parts[1] + parts[0] + parts[2] === key)) break;
            const q = [p[2], p[3], p[4], p[5]].map((x) => Math.round(parseFloat(x) * 100));
            if (q.every((x) => Number.isFinite(x) && x > 0)) { add("NIFTY500", day, q[0], q[1], q[2], q[3], 0); found = true; }
            break;
          }
        }
        if (!found && bh.status === 200 && Object.values(bars).some((b) => b.d.includes(day))) {
          issues.push(`The Nifty 500 index value for ${dstr} could not be read.`);
        }
      }
    }
    if (yahooDown > 0) issues.push(`Yahoo's split list could not be reached; ${yahooDown} possible split(s) were not confirmed.`);
    const rows = Object.values(bars);
    for (let i = 0; i < rows.length; i += 40) {
      const r = await sb.rpc("cc_put_bars", { p_token: null, p_rows: rows.slice(i, i + 40) });
      if (r.error) { issues.push(`Saving prices failed: ${r.error.message}`); return await finish({ ok: false }, 500); }
    }
    if (events.length) {
      const r = await sb.rpc("cc_put_events", { p_token: null, p_rows: events });
      if (r.error) issues.push(`Saving split events failed: ${r.error.message}`);
    }
    if (review.length) await sb.rpc("cc_put_review", { p_token: null, p_rows: review });
    return await finish({ ok: true, from: last, to: newest, days, rows: rows.length, events, review });
  } catch (e) {
    issues.push(`The nightly update crashed: ${String(e).slice(0, 200)}`);
    return await finish({ ok: false }, 500);
  }
});
