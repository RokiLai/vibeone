// VibeOne Service Worker — Offline App Shell Cache & Web Push Notifications (iOS 16.4+ PWA / Android / Desktop)

const CACHE_NAME = 'vibeone-shell-v1';
const PRECACHE_URLS = [
  '/',
  '/manifest.json?v=3',
  '/favicon.png?v=3',
  '/icon-192.png?v=3',
  '/apple-touch-icon.png?v=3',
  '/vendor/tailwindcss.min.js',
  '/vendor/github-dark.min.css',
  '/vendor/purify.min.js',
  '/vendor/marked.min.js',
  '/vendor/highlight.min.js',
  '/static/app.css',
  '/static/app-bootstrap.js',
  '/static/app-sidebar-gestures.js',
  '/static/app-sidebar.js',
  '/static/app-chat.js',
  '/static/app-pickers.js',
  '/static/app-websocket.js',
  '/static/app-auth.js',
  '/static/app-composer.js',
  '/static/app-picker-events.js',
  '/static/app-profiles.js',
  '/static/app-settings.js',
  '/static/app-image-viewer.js',
  '/static/app-file-tools.js',
  '/static/app.js',
];

const CACHE_FIRST_PREFIXES = ['/vendor/'];
const CACHE_FIRST_PATHS = new Set([
  '/manifest.json',
  '/favicon.png',
  '/favicon.ico',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
  '/icon.svg',
]);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const { pathname } = url;
  if (
    pathname.startsWith('/api/') ||
    pathname === '/ws' ||
    pathname.startsWith('/uploads/') ||
    pathname === '/sw.js'
  ) {
    return;
  }

  // Navigation / index.html: Network-First with offline fallback to cached '/'
  if (request.mode === 'navigate' || pathname === '/') {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put('/', copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => caches.match('/').then((cached) => cached || caches.match(request)))
    );
    return;
  }

  // App static assets (/static/*): Network-First so updates apply immediately online, cached offline
  if (pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Vendor & icons: Cache-First with network fallback
  if (
    CACHE_FIRST_PREFIXES.some((p) => pathname.startsWith(p)) ||
    CACHE_FIRST_PATHS.has(pathname)
  ) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return resp;
        });
      })
    );
  }
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: 'VibeOne', body: event.data ? event.data.text() : '有新的会话动态' };
  }

  const title = payload.title || 'VibeOne';
  const options = {
    body: payload.body || '任务状态已更新',
    icon: '/icon-192.png?v=3',
    badge: '/icon-192.png?v=3',
    tag: payload.tag || (payload.session_id ? `vibeone-${payload.session_id}` : 'vibeone'),
    renotify: true,
    data: {
      url: payload.url || '/',
      session_id: payload.session_id || '',
      profile: payload.profile || '',
    },
  };

  // IMPORTANT (iOS WebKit requirement): always showNotification() on push events
  // to avoid Apple revoking the push subscription; foreground suppression is handled server-side.
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const targetSid = data.session_id || '';
  const targetProfile = data.profile || '';
  const qs = [];
  if (targetProfile) qs.push(`profile=${encodeURIComponent(targetProfile)}`);
  if (targetSid) qs.push(`session=${encodeURIComponent(targetSid)}`);
  const targetUrl = qs.length ? `/?${qs.join('&')}` : (data.url || '/');

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          try {
            client.postMessage({
              type: 'open_session',
              session_id: targetSid,
              profile: targetProfile,
            });
          } catch (_) {}
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return null;
    })
  );
});
