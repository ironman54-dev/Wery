/* One place for every form on the site: waitlists (Nexus, Codeflow, GreekWatch) and Contact.
   Talks to Supabase through functions only (sql/cc_backend.sql). The anon key below is public by design:
   the tables have row-level security with no policies, so the browser can only call these three functions. */
(function () {
  var SB = 'https://mmbdijtyvgzhmnlcddki.supabase.co';
  var KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1tYmRpanR5dmd6aG1ubGNkZGtpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTU0NTgxNDIsImV4cCI6MjExMTAzNDE0Mn0.0OQcGYMjAlAKWCUGXHjTd1zDcxid5MCdQFGZmGC_jvI';
  function rpc(fn, body) {
    return fetch(SB + '/rest/v1/rpc/' + fn, { method: 'POST', headers: { apikey: KEY, Authorization: 'Bearer ' + KEY, 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { if (!r.ok) { var e = new Error(fn + ' ' + r.status); e.status = r.status; throw e; } return r.json(); });
  }
  window.CCApi = {
    /* {product:'nexus'|'codeflow'|'greekwatch', name, email, mobile?, ref?, suggestion?} -> {place, code, priority} */
    join: function (o) {
      return rpc('cc_join_waitlist', { p_product: o.product, p_name: o.name || '', p_email: o.email, p_mobile: o.mobile || null, p_ref: o.ref || null, p_suggestion: o.suggestion || null });
    },
    /* daily bars + split/bonus events for the stock chart: {d,o,h,l,c,v,ev} (prices in paise) */
    bars: function (symbol) { return rpc('cc_stock_bars', { p_symbol: symbol }); },
    health: function () { return rpc('cc_health_status', {}); },
    count: function (product) { return rpc('cc_waitlist_count', { p_product: product }); },
    /* {name, email, topic, message} */
    contact: function (o) {
      return rpc('cc_contact_submit', { p_name: o.name, p_email: o.email, p_topic: o.topic || '', p_message: o.message });
    }
  };
})();
