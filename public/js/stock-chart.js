/* Stock chart for the screener card. Daily / Weekly / Monthly, Candles / Line / Area, 50-150-200 DMA, volume.
   Data: NSE end-of-day bars from Supabase (CCApi.bars). Prices are raw in the database; this file adjusts them for
   stock splits and bonus issues using the stored events, so old prices line up with today's. Not adjusted for dividends. */
(function () {
  var cache = {};
  var live = null; // the chart currently on screen

  function cssVar(n, fb) { var v = getComputedStyle(document.documentElement).getPropertyValue(n).trim(); return v || fb; }
  function theme() {
    var dark = document.documentElement.dataset.theme === 'dark';
    return {
      dark: dark, text: cssVar('--text-2', dark ? '#b9b2a2' : '#4a5d52'), grid: dark ? 'rgba(255,255,255,.06)' : 'rgba(9,25,18,.07)',
      border: dark ? 'rgba(255,255,255,.14)' : 'rgba(9,25,18,.16)', acc: cssVar('--accent', '#0d5c3e'),
      up: '#17a673', down: '#e0475b', volUp: dark ? 'rgba(23,166,115,.35)' : 'rgba(23,166,115,.30)', volDown: dark ? 'rgba(224,71,91,.35)' : 'rgba(224,71,91,.30)'
    };
  }
  function iso(day) { return new Date(day * 86400000).toISOString().slice(0, 10); }

  // raw paise arrays + split/bonus events -> adjusted daily bars
  function toBars(raw) {
    var n = raw.d.length, ev = (raw.ev || []).map(function (e) { return [e[0], e[1]]; }).sort(function (a, b) { return a[0] < b[0] ? -1 : 1; });
    var out = new Array(n), k = ev.length - 1, f = 1;
    for (var i = n - 1; i >= 0; i--) {
      var date = iso(raw.d[i]);
      while (k >= 0 && ev[k][0] > date) { f *= ev[k][1]; k--; }   // events after this bar change older prices
      out[i] = { t: date, o: raw.o[i] / 100 * f, h: raw.h[i] / 100 * f, l: raw.l[i] / 100 * f, c: raw.c[i] / 100 * f, v: Math.round(raw.v[i] / f) };
    }
    return out;
  }
  function sma(bars, len) {
    var out = [], sum = 0;
    for (var i = 0; i < bars.length; i++) {
      sum += bars[i].c; if (i >= len) sum -= bars[i - len].c;
      out.push(i >= len - 1 ? sum / len : null);
    }
    return out;
  }
  function weekKey(t) { var d = new Date(t + 'T00:00:00Z'); var day = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - day); return d.toISOString().slice(0, 10); }
  function group(bars, tf, dmas) {
    if (tf === 'D') return { bars: bars, dmas: dmas };
    var keyf = tf === 'W' ? function (b) { return weekKey(b.t); } : function (b) { return b.t.slice(0, 7); };
    var out = [], idx = [], cur = null, curKey = '';
    bars.forEach(function (b, i) {
      var k = keyf(b);
      if (k !== curKey) { cur = { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v }; out.push(cur); idx.push(i); curKey = k; }
      else { cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v; idx[idx.length - 1] = i; }
    });
    var g = {}; Object.keys(dmas).forEach(function (n) { g[n] = idx.map(function (i) { return dmas[n][i]; }); });
    return { bars: out, dmas: g };
  }
  function fmt(v) { return v >= 1000 ? v.toLocaleString('en-IN', { maximumFractionDigits: 1 }) : v.toFixed(2); }
  function fmtVol(v) { return v >= 1e7 ? (v / 1e7).toFixed(2) + ' Cr' : v >= 1e5 ? (v / 1e5).toFixed(2) + ' L' : v.toLocaleString('en-IN'); }

  function mount(el, symbol, asOf) {
    if (live) { try { live.destroy(); } catch (e) {} live = null; }
    var LWC = window.LightweightCharts;
    el.innerHTML = '<div class="cc-ch-bar"><div class="cc-ch-seg" data-g="tf"><button data-v="D" class="on">D</button><button data-v="W">W</button><button data-v="M">M</button></div>' +
      '<div class="cc-ch-seg" data-g="ty"><button data-v="candle" class="on">Candles</button><button data-v="line">Line</button><button data-v="area">Area</button></div>' +
      '<div class="cc-ch-seg" data-g="rg"><button data-v="63">3M</button><button data-v="126">6M</button><button data-v="252" class="on">1Y</button><button data-v="756">3Y</button><button data-v="1260">5Y</button><button data-v="0">All</button></div>' +
      '<div class="cc-ch-seg" data-g="ma"><button data-v="50" class="on">50</button><button data-v="150" class="on">150</button><button data-v="200" class="on">200</button></div></div>' +
      '<div class="cc-health"></div><div class="cc-ch-leg"></div><div class="cc-ch-box"></div><div class="cc-ch-note"></div>';
    if (window.CCHealth) CCHealth.render(el.querySelector('.cc-health'));
    var box = el.querySelector('.cc-ch-box'), leg = el.querySelector('.cc-ch-leg'), note = el.querySelector('.cc-ch-note');
    box.innerHTML = '<div class="cc-ch-msg">Loading chart…</div>';
    var state = { tf: 'D', ty: 'candle', rg: 252, ma: { 50: true, 150: true, 200: true } };
    var chart = null, ro = null, dead = false, daily = null, dmas = null;

    function draw() {
      if (dead || !daily) return;
      var t = theme(), g = group(daily, state.tf, dmas), bars = g.bars;
      if (chart) { chart.remove(); chart = null; }
      box.innerHTML = '';
      chart = LWC.createChart(box, {
        autoSize: true,
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: t.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 10, attributionLogo: false },
        grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.border, scaleMargins: { top: 0.06, bottom: 0.24 } },
        timeScale: { borderColor: t.border, rightOffset: 4, fixLeftEdge: true },
        crosshair: { mode: LWC.CrosshairMode.Normal },
        handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: true }, axisDoubleClickReset: true }
      });
      var main;
      if (state.ty === 'candle') {
        main = chart.addCandlestickSeries({ upColor: t.up, downColor: t.down, borderUpColor: t.up, borderDownColor: t.down, wickUpColor: t.up, wickDownColor: t.down, priceLineVisible: true });
        main.setData(bars.map(function (b) { return { time: b.t, open: b.o, high: b.h, low: b.l, close: b.c }; }));
      } else if (state.ty === 'area') {
        main = chart.addAreaSeries({ lineColor: t.acc, topColor: t.acc + '55', bottomColor: t.acc + '00', lineWidth: 2 });
        main.setData(bars.map(function (b) { return { time: b.t, value: b.c }; }));
      } else {
        main = chart.addLineSeries({ color: t.acc, lineWidth: 2 });
        main.setData(bars.map(function (b) { return { time: b.t, value: b.c }; }));
      }
      var vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      vol.setData(bars.map(function (b) { return { time: b.t, value: b.v, color: b.c >= b.o ? t.volUp : t.volDown }; }));
      var cols = { 50: '#e0a21b', 150: '#3b82f6', 200: '#a855f7' };
      [50, 150, 200].forEach(function (n) {
        if (!state.ma[n]) return;
        var s = chart.addLineSeries({ color: cols[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: '' });
        var data = []; g.dmas[n].forEach(function (v, i) { if (v != null) data.push({ time: bars[i].t, value: v }); });
        s.setData(data);
      });
      // visible range = last N daily sessions, converted to the timeframe
      var per = state.tf === 'D' ? 1 : state.tf === 'W' ? 5 : 21;
      var count = state.rg ? Math.max(8, Math.round(state.rg / per)) : bars.length;
      var from = Math.max(0, bars.length - count);
      chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: bars.length + 3 });
      var last = bars[bars.length - 1];
      function showLeg(b, prev) {
        if (!b) return;
        var ch = prev ? (b.c / prev.c - 1) * 100 : null;
        leg.innerHTML = '<b>' + symbol + '</b> <span>' + b.t + '</span> O ' + fmt(b.o) + ' H ' + fmt(b.h) + ' L ' + fmt(b.l) + ' C <b>' + fmt(b.c) + '</b>' +
          (ch != null ? ' <span class="' + (ch >= 0 ? 'cc-up' : 'cc-dn') + '">' + (ch >= 0 ? '+' : '') + ch.toFixed(2) + '%</span>' : '') + ' &nbsp; Vol ' + fmtVol(b.v);
      }
      var byT = {}; bars.forEach(function (b, i) { byT[b.t] = i; });
      showLeg(last, bars[bars.length - 2]);
      chart.subscribeCrosshairMove(function (p) {
        if (!p || !p.time || byT[p.time] == null) { showLeg(last, bars[bars.length - 2]); return; }
        var i = byT[p.time]; showLeg(bars[i], bars[i - 1]);
      });
      note.innerHTML = 'Prices are adjusted for stock splits and bonus issues, so older prices line up with today’s. Not adjusted for dividends. ' +
        'Moving averages are daily (50, 150, 200 sessions). Source: NSE end-of-day files. Last bar: ' + daily[daily.length - 1].t + '.' + (asOf ? ' The rule check below is as of the screener date, ' + asOf + '.' : '');
    }

    el.querySelectorAll('.cc-ch-seg').forEach(function (seg) {
      seg.addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var g = seg.dataset.g, v = b.dataset.v;
        if (g === 'ma') { b.classList.toggle('on'); state.ma[v] = b.classList.contains('on'); }
        else {
          seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
          if (g === 'tf') { state.tf = v; state.rg = v === 'D' ? 252 : v === 'W' ? 1260 : 0; el.querySelector('[data-g=rg]').querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', +x.dataset.v === state.rg); }); }
          if (g === 'ty') state.ty = v;
          if (g === 'rg') state.rg = +v;
        }
        draw();
      });
    });

    var p = cache[symbol] ? Promise.resolve(cache[symbol]) : CCApi.bars(symbol).then(function (r) { cache[symbol] = r; return r; });
    p.then(function (raw) {
      if (dead) return;
      if (!raw || !raw.d || raw.d.length < 20) { box.innerHTML = '<div class="cc-ch-msg">No chart data for this stock yet.</div>'; return; }
      daily = toBars(raw);
      dmas = { 50: sma(daily, 50), 150: sma(daily, 150), 200: sma(daily, 200) };
      draw();
      ro = new MutationObserver(function () { draw(); });   // redraw on light/dark change
      ro.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }).catch(function () { box.innerHTML = '<div class="cc-ch-msg">The chart could not load right now. Please try again.</div>'; });

    live = { destroy: function () { dead = true; if (ro) ro.disconnect(); if (chart) chart.remove(); } };
  }
  window.CCChart = { mount: mount, adjust: toBars };
})();
