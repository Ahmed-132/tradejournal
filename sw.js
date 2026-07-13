/* Offline cache for the installed PWA.
   Strategy:
   - Navigations (the HTML page): network-first, fall back to cache when offline.
     This guarantees updates show up while online, yet the app still opens offline.
   - Static assets (css/js/icons): cache-first for speed, updated in the background. */
const CACHE = 'tradejournal-v4';
const CORE = ['./', './index.html', './styles.css?v=2', './app.js?v=2', './manifest.webmanifest',
  './assets/icon.svg', './assets/apple-touch-icon.png', './assets/icon-192.png', './assets/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const isNavigation = req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');

  if (isNavigation) {
    // Network-first so a fresh page is served whenever online.
    e.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put('./index.html', copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  // Static assets: cache-first, refresh in background.
  e.respondWith(
    caches.match(req).then(cached => {
      const network = fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => cached);
      return cached || network;
    })
  );
});
