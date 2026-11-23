/* Loaded by /nexus/, /codeflow/ and /greekwatch/. These pages live inside the main site's frame:
   opened on their own they bounce to the main site with the right tab; inside the frame they follow its light/dark theme. */
(function () {
  var site = document.currentScript.getAttribute('data-site');
  if (window.top === window.self) { location.replace('/' + (site === 'nexus' ? location.search : '') + '#' + site); return; }
  var h = document.documentElement;
  h.classList.add('emb');
  addEventListener('message', function (e) {
    if (e.origin === location.origin && e.data && e.data.cc === 'theme') h.setAttribute('data-theme', e.data.theme === 'dark' ? 'dark' : 'light');
  });
  try { parent.postMessage({ cc: 'ready' }, location.origin); } catch (e) {}
})();
