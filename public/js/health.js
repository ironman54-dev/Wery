/* Red "Data issue" pill. Shows a plain-words list of anything wrong with the data feeds, while the data itself stays visible.
   Sources: the nightly NSE price update (cc_health_status in Supabase) and the weekly screener's own status file (data/status.json).
   Nothing is shown in red when everything is fine; a quiet green "Data OK" note says so. */
(function () {
  var cache = null, cacheAt = 0, inflight = null;
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function day(n) { var d = new Date(n * 86400000); return d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; }
  function ts(t) { var d = new Date(t); return d.getDate() + ' ' + MON[d.getMonth()] + ', ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function load() {
    if (cache && Date.now() - cacheAt < 60000) return Promise.resolve(cache);
    if (inflight) return inflight;
    inflight = Promise.all([
      CCApi.health().catch(function () { return null; }),
      fetch('/data/status.json?v=' + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (res) {
      var h = res[0], s = res[1], issues = [], now = Date.now();
      if (!h) issues.push('The price database did not answer, so the charts may not load.');
      else {
        var srcs = h.sources || [];
        if (!srcs.length) issues.push('The nightly NSE price update has not reported yet.');
        srcs.forEach(function (x) {
          if ((now - new Date(x.ts)) / 36e5 > 36) issues.push('The nightly NSE price update has not run since ' + ts(x.ts) + '.');
          (x.issues || []).forEach(function (t) { issues.push(t); });
        });
        if (h.review && h.review.n > 0) issues.push(h.review.n + ' possible stock split(s) are waiting for a check (' + h.review.symbols + '). Those stocks are held out of the weekly screener until checked.');
        if (h.last_day && now / 864e5 - h.last_day > 4.5) issues.push('No new NSE prices since ' + day(h.last_day) + '.');
      }
      if (!s) issues.push('The screener status file could not be read.');
      else {
        if (s.updated_utc && (now - new Date(s.updated_utc)) / 864e5 > 8.5) issues.push('The weekly screener update is overdue. The list shows data up to ' + s.market_date + '.');
        (s.issues || []).forEach(function (t) { issues.push(t); });
      }
      cache = { issues: issues, last: h && h.last_day, week: s && s.market_date };
      cacheAt = Date.now(); inflight = null;
      return cache;
    });
    return inflight;
  }

  function render(el) {
    if (!el) return;
    load().then(function (c) {
      if (!c.issues.length) {
        el.innerHTML = '<span class="cc-pill ok" title="The nightly price update and the weekly screener update both reported no problems.">&#9679; Data OK' + (c.last ? ' &middot; NSE prices to ' + day(c.last) : '') + '</span>';
        return;
      }
      var first = c.issues[0], short = first.length > 120 ? first.slice(0, 117) + '…' : first;
      var label = c.issues.length === 1 ? 'Data issue: ' + short : 'Data issues (' + c.issues.length + '): ' + short;
      el.innerHTML = '<button type="button" class="cc-pill bad" aria-expanded="false">&#9888; ' + esc(label) + ' <u>' + (c.issues.length > 1 || first.length > 120 ? 'details' : 'what this means') + '</u></button>' +
        '<div class="cc-pill-detail" hidden><ul>' + c.issues.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>' +
        '<p>You can still use everything on the page. Numbers shown are the latest that passed the checks. The owner is told by the failed run; nothing needs doing on your side.</p></div>';
      var btn = el.querySelector('button'), det = el.querySelector('.cc-pill-detail');
      btn.addEventListener('click', function () { det.hidden = !det.hidden; btn.setAttribute('aria-expanded', String(!det.hidden)); });
    });
  }
  window.CCHealth = { render: render, load: load, reset: function () { cache = null; inflight = null; } };
})();
