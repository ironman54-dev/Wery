/* waitlist (shared backend: /js/cc-api.js) */
document.querySelectorAll('[data-wl]').forEach(form => {
  const msg = form.querySelector('.wl-msg');
  const input = form.querySelector('input[type=email]');
  const btn = form.querySelector('button');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const email = input.value.trim();
    const name = form.querySelector('[name=name]') ? form.querySelector('[name=name]').value.trim() : '';
    const suggestion = form.querySelector('[name=suggestion]') ? form.querySelector('[name=suggestion]').value.trim() : '';
    msg.className = 'wl-msg';
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { msg.textContent = 'Enter a valid email.'; msg.classList.add('err'); input.focus(); return; }
    btn.disabled = true; msg.textContent = 'Saving your spot…';
    try {
      await CCApi.join({ product: 'codeflow', name, email, suggestion });
      document.querySelectorAll('[data-wl]').forEach(f => { f.classList.add('done'); f.querySelectorAll('input,textarea').forEach(x => x.style.display = 'none'); f.querySelector('.wl-msg').textContent = (name ? "You're in line, " + name + '. ' : "You're in line. ") + "You get priority access when CodeFlow opens, and we email you first." + (suggestion ? ' Thank you for the suggestion.' : ''); f.querySelector('.wl-msg').className = 'wl-msg'; });
    } catch (err) {
      btn.disabled = false; msg.textContent = 'Something went wrong. Please try again.'; msg.classList.add('err');
    }
  });
});

/* nav border on scroll */
const nav = document.getElementById('nav');
const onScroll = () => nav.classList.toggle('scrolled', scrollY > 8);
addEventListener('scroll', onScroll, { passive: true }); onScroll();

/* reveal on scroll (content stays visible if JS or IntersectionObserver is missing) */
const els = [...document.querySelectorAll('.rv')];
if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver(es => es.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } }), { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
  els.forEach((el, i) => { el.style.transitionDelay = (i % 4) * 60 + 'ms'; io.observe(el); });
} else els.forEach(el => el.classList.add('in'));

/* hero frame leans toward the pointer (desktop, no reduced motion) */
const fine = matchMedia('(hover:hover) and (pointer:fine) and (min-width:981px) and (prefers-reduced-motion:no-preference)').matches;
const hf = document.getElementById('heroFrame');
if (fine && hf) {
  const stage = hf.parentElement;
  stage.addEventListener('pointermove', e => {
    const r = stage.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
    hf.style.transform = `perspective(1600px) rotateY(${-5 + x * 5}deg) rotateX(${2 - y * 4}deg)`;
  });
  stage.addEventListener('pointerleave', () => { hf.style.transform = ''; });
}


/* dial rings (brand motif) */
document.querySelectorAll('svg.ring').forEach(svg => {
  let out = '';
  for (let i = 0; i < 120; i++) {
    const a = i / 120 * Math.PI * 2, long = i % 5 === 0, r1 = long ? 236 : 248, r2 = 268;
    out += `<line class="${long ? 'l' : ''}" x1="${(Math.cos(a) * r1).toFixed(1)}" y1="${(Math.sin(a) * r1).toFixed(1)}" x2="${(Math.cos(a) * r2).toFixed(1)}" y2="${(Math.sin(a) * r2).toFixed(1)}"/>`;
  }
  svg.innerHTML = out;
});

/* stat count-up */
const nums = document.querySelectorAll('.stat b[data-n]');
if ('IntersectionObserver' in window) {
  const cio = new IntersectionObserver(es => es.forEach(en => {
    if (!en.isIntersecting) return; cio.unobserve(en.target);
    const el = en.target, to = +el.dataset.n, t0 = performance.now();
    (function tick(t) { const p = Math.min((t - t0) / 1400, 1); el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3))); if (p < 1) requestAnimationFrame(tick); })(t0);
  }), { threshold: 0.6 });
  nums.forEach(n => cio.observe(n));
} else nums.forEach(n => n.textContent = n.dataset.n);

/* strategy list cycles */
const lis = [...document.querySelectorAll('#stratList li')];
if (lis.length) { let k = 0; lis[0].classList.add('on'); setInterval(() => { lis[k].classList.remove('on'); k = (k + 1) % lis.length; lis[k].classList.add('on'); }, 1700); }

/* card spotlight follows the pointer */
document.querySelectorAll('.app,.mini').forEach(c => c.addEventListener('pointermove', e => {
  const r = c.getBoundingClientRect(); c.style.setProperty('--mx', (e.clientX - r.left) + 'px'); c.style.setProperty('--my', (e.clientY - r.top) + 'px');
}));

/* ===================== feature graphics ===================== */
const rng = seed => () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const poly = pts => 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L');
const inits = {};

/* audit card: ticks one by one */
inits.audit = card => { card.querySelectorAll('.au').forEach((r, i) => setTimeout(() => r.classList.add('on'), 500 + i * 650)); };

/* 02 study on study */
inits.study = panel => {
  const svg = panel.querySelector('svg'), R = rng(7), N = 70, X0 = 14, X1 = 506;
  const x = i => X0 + i * (X1 - X0) / (N - 1), y = v => 20 + (80 - v) / 60 * 200;
  const rsi = []; let v = 52;
  for (let i = 0; i < N; i++) { v += (R() - .5) * 11 + Math.sin(i / 5) * 2.2; v = Math.max(26, Math.min(76, v)); rsi.push(v); }
  const ema = []; const k = 2 / 10; rsi.forEach((r, i) => ema.push(i ? r * k + ema[i - 1] * (1 - k) : r));
  [30, 50, 70].forEach(l => { el('line', { class: 'grid', x1: X0, x2: X1, y1: y(l), y2: y(l) }, svg); const t = el('text', { class: 'lbl', x: X1 - 14, y: y(l) - 4 }, svg); t.textContent = l; });
  el('path', { class: 'line l-ivory', pathLength: 1, d: poly(rsi.map((r, i) => [x(i), y(r)])) }, svg);
  el('path', { class: 'line l-lime', pathLength: 1, d: poly(ema.map((r, i) => [x(i), y(r)])) }, svg);
  // trendline through two swing highs
  const half = N >> 1; let a = 0, b = half; rsi.forEach((r, i) => { if (i < half && r > rsi[a]) a = i; if (i >= half && r > rsi[b]) b = i; });
  const m = (y(rsi[b]) - y(rsi[a])) / (x(b) - x(a)), ya = y(rsi[a]) + m * (X0 - x(a)), yb = y(rsi[a]) + m * (X1 - x(a));
  el('path', { class: 'line l-dash', pathLength: 1, d: `M${X0} ${ya.toFixed(1)} L${X1} ${yb.toFixed(1)}` }, svg);
  const tg = el('text', { class: 'tag', x: X0 + 6, y: 16 }, svg); tg.textContent = 'EMA(9) on RSI';
  const chips = panel.querySelectorAll('.chips em'); let c = 0;
  setInterval(() => { chips.forEach(e => e.classList.remove('on')); chips[0].classList.add('on'); chips[(c++ % 3) + 1].classList.add('on'); }, 1800);
};

/* 03 replay gym */
inits.replay = panel => {
  const svg = panel.querySelector('svg'), lab = panel.querySelector('#barc'), R = rng(21), N = 28, W = 520, H = 250;
  const bars = []; let p = 100;
  for (let i = 0; i < N; i++) { const o = p, drift = i < 12 ? (R() - .5) * 8 + (i > 8 ? 1.5 : 0) : 1.6 + R() * 3.4; const c = o + drift, hi = Math.max(o, c) + R() * 3.5, lo = Math.min(o, c) - R() * 3.5; bars.push({ o, c, hi, lo }); p = c; }
  const entry0 = bars[11].c, stop0 = Math.min(...bars.slice(6, 12).map(b => b.lo)) - 1;
  const min = Math.min(...bars.map(b => b.lo)) - 3, max = Math.max(entry0 + (entry0 - stop0) * 2.4, ...bars.map(b => b.hi)) + 3;
  const y = v => 12 + (max - v) / (max - min) * (H - 24), bw = (W - 40) / N, x = i => 20 + i * bw + bw / 2;
  const entry = bars[11].c, stop = Math.min(...bars.slice(6, 12).map(b => b.lo)) - 1, target = entry + (entry - stop) * 2.4;
  const zones = el('g', { opacity: 0 }, svg);
  el('rect', { class: 'zone-t', x: x(11), y: y(target), width: W - x(11) - 10, height: y(entry) - y(target) }, zones);
  el('rect', { class: 'zone-s', x: x(11), y: y(entry), width: W - x(11) - 10, height: y(stop) - y(entry) }, zones);
  [[target, 'TARGET'], [entry, 'ENTRY'], [stop, 'STOP']].forEach(([v, t]) => { el('line', { x1: x(11), x2: W - 10, y1: y(v), y2: y(v), stroke: t === 'ENTRY' ? '#F3EEDF' : t === 'TARGET' ? '#C8F169' : '#8FA89A', 'stroke-width': 1.4, 'stroke-dasharray': '5 4' }, zones); const tx = el('text', { class: 'tag', x: W - 66, y: y(v) - 5, fill: '#fff' }, zones); tx.textContent = t; tx.style.fill = t === 'STOP' ? '#8FA89A' : t === 'ENTRY' ? '#F3EEDF' : '#C8F169'; });
  const cg = el('g', {}, svg), drawn = [];
  const draw = n => { cg.innerHTML = ''; for (let i = 0; i < n; i++) { const b = bars[i], up = b.c >= b.o, col = up ? '#F3EEDF' : '#8FA89A'; el('line', { x1: x(i), x2: x(i), y1: y(b.hi), y2: y(b.lo), stroke: col, 'stroke-width': 1.4 }, cg); el('rect', { x: x(i) - bw * .3, width: bw * .6, y: y(Math.max(b.o, b.c)), height: Math.max(2, Math.abs(y(b.o) - y(b.c))), fill: col, rx: 1 }, cg); } };
  let n = 0, hold = 0;
  setInterval(() => {
    if (hold > 0) { hold--; return; }
    n++; if (n > N) { n = 0; zones.setAttribute('opacity', 0); hold = 4; }
    draw(n); lab.textContent = `Bar ${n} / ${N}`; if (n >= 12) zones.setAttribute('opacity', 1); if (n === N) hold = 12;
  }, 170);
};

/* 04 exit ratchet */
inits.exit = panel => {
  const svg = panel.querySelector('svg'), ph = [...panel.querySelectorAll('.phases em')];
  const price = [[20, 190], [50, 178], [80, 186], [110, 168], [140, 150], [165, 158], [195, 128], [225, 118], [255, 96], [285, 104], [315, 78], [345, 70], [375, 56], [405, 64], [435, 46], [465, 52], [500, 36]];
  const stop = [[20, 206], [135, 206], [135, 176], [250, 176], [250, 150], [330, 150], [330, 132], [380, 110], [435, 82], [500, 70]];
  el('line', { class: 'grid', x1: 20, x2: 500, y1: 190, y2: 190 }, svg);
  const t = el('text', { class: 'lbl', x: 24, y: 184 }, svg); t.textContent = 'entry';
  el('path', { class: 'line l-lime', pathLength: 1, d: poly(stop), style: 'transition-delay:.4s;stroke-width:2.2;stroke-dasharray:1' }, svg);
  el('path', { class: 'line l-ivory', pathLength: 1, d: poly(price), style: 'transition:stroke-dashoffset 4s var(--ease)' }, svg);
  [[135, 'BE+0.15R'], [250, '+1R'], [380, 'ATR']].forEach(([cx, s]) => { const g = el('text', { class: 'lbl', x: cx - 8, y: 232 }, svg); g.textContent = s; });
  let i = 0; const step = () => { ph.forEach((e, k) => e.classList.toggle('on', k <= i)); i = (i + 1) % 6; if (i === 5) i = 0; };
  setInterval(step, 1400); step();
};

/* 05 monte carlo */
inits.mc = panel => {
  const cv = panel.querySelector('canvas'), ctx = cv.getContext('2d'), W = cv.width, H = cv.height, R = rng(99), P = 250, S = 80;
  const paths = []; for (let k = 0; k < P; k++) { let v = 100; const a = [v]; for (let s = 1; s <= S; s++) { v *= 1 + (R() - .47) * 0.06 + 0.004; a.push(v); } paths.push(a); }
  const all = paths.flat(), lo = Math.min(...all) * .96, hi = Math.max(...all) * 1.02;
  const X = s => 30 + s / S * (W - 60), Y = v => H - 30 - (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * (H - 60);
  const pct = (s, q) => { const col = paths.map(p => p[s]).sort((a, b) => a - b); return col[Math.floor(q * (P - 1))]; };
  const bands = { p5: [], p50: [], p95: [] }; for (let s = 0; s <= S; s++) { bands.p5.push(pct(s, .05)); bands.p50.push(pct(s, .5)); bands.p95.push(pct(s, .95)); }
  let f = 0;
  const frame = () => {
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(143,168,154,.2)'; ctx.lineWidth = 1; ctx.setLineDash([4, 6]); [0, 1, 2, 3].forEach(i => { const yy = 30 + i * (H - 60) / 3; ctx.beginPath(); ctx.moveTo(30, yy); ctx.lineTo(W - 30, yy); ctx.stroke(); }); ctx.setLineDash([]);
    const upto = Math.min(S, Math.floor(f / 2));
    ctx.lineWidth = 1.2; ctx.strokeStyle = 'rgba(243,238,223,.13)';
    paths.forEach(p => { ctx.beginPath(); for (let s = 0; s <= upto; s++) { s ? ctx.lineTo(X(s), Y(p[s])) : ctx.moveTo(X(s), Y(p[s])); } ctx.stroke(); });
    const line = (arr, col, w) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.beginPath(); for (let s = 0; s <= upto; s++) { s ? ctx.lineTo(X(s), Y(arr[s])) : ctx.moveTo(X(s), Y(arr[s])); } ctx.stroke(); };
    if (upto > 6) { line(bands.p5, '#8FA89A', 3); line(bands.p95, '#F3EEDF', 3); line(bands.p50, '#C8F169', 5); }
    ctx.font = '600 20px JetBrains Mono, monospace'; if (upto >= S) { ctx.fillStyle = '#C8F169'; ctx.fillText('P50', W - 74, Y(bands.p50[S]) - 10); ctx.fillStyle = '#F3EEDF'; ctx.fillText('P95', W - 74, Y(bands.p95[S]) - 10); ctx.fillStyle = '#8FA89A'; ctx.fillText('P5', W - 60, Y(bands.p5[S]) + 24); }
    f++; if (f > S * 2 + 120) f = 0;
    requestAnimationFrame(frame);
  };
  frame();
};

/* 06 mcp terminal */
inits.mcp = panel => {
  const out = panel.querySelector('#mcpOut'), tools = [...panel.querySelectorAll('.tools em')];
  const script = [
    ['d', '// your agent asks CodeFlow'], ['k', '→ tools/call  get market breadth'], ['d', '← regime, participation, follow-through-day state'],
    ['k', '→ tools/call  scan patterns'], ['d', '← setups that qualify today, with pivot and stop'],
    ['k', '→ tools/call  run monte carlo'], ['d', '← 250 paths, percentile curves, risk of ruin'],
  ];
  const map = { 'get market breadth': 4, 'scan patterns': 0, 'run monte carlo': 5 };
  const run = async () => {
    for (;;) {
      out.innerHTML = '';
      for (const [cls, line] of script) {
        const span = document.createElement('span'); span.className = cls; out.appendChild(span); out.appendChild(document.createTextNode('\n'));
        for (const ch of line) { span.textContent += ch; await new Promise(r => setTimeout(r, 16)); }
        const hit = Object.keys(map).find(k => line.includes(k)); if (hit) { tools.forEach(t => t.classList.remove('on')); tools[map[hit]].classList.add('on'); }
        await new Promise(r => setTimeout(r, cls === 'k' ? 350 : 650));
      }
      const c = document.createElement('i'); c.className = 'caret'; out.appendChild(c);
      await new Promise(r => setTimeout(r, 3200));
    }
  };
  run();
};

/* start each graphic once, when it scrolls into view */
const gio = new IntersectionObserver(es => es.forEach(en => {
  if (!en.isIntersecting) return; gio.unobserve(en.target);
  const el = en.target; el.classList.add('go'); const f = inits[el.dataset.g]; if (f) f(el);
}), { threshold: 0.3 });
document.querySelectorAll('[data-g]').forEach(n => gio.observe(n));
