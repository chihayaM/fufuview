/* fufuView Pro · Service Worker */
const CACHE_NAME = 'fufuview-v4';
const PRECACHE = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/script.js',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // API 请求不缓存
  if (url.pathname.startsWith('/api/')) return;
  // 静态资源：cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(resp => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
          }
          return resp;
        });
      })
    );
    return;
  }
  // 页面：network-first
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
