// 曾科的期货工具 - Service Worker
// 提供离线缓存能力，首次加载后断网仍可使用静态框架
// v25: 数据类资源改为「网络优先 + 失败回落旧缓存」，避免网关抖动(502)时页面打不开

const CACHE_NAME = 'qb-rb-v34';
const DATA_CACHE = 'qb-rb-data-v25';

// 新版本激活时清理旧缓存
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME && k !== DATA_CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// 网络优先；失败时回落到「上一次成功取到的数据」
function networkFirstWithFallback(event, cacheName) {
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.ok) {
          const clone = response.clone();
          caches.open(cacheName).then(c => c.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => {
        if (cached) return cached;
        if (event.request.mode === 'navigate') return caches.match('./index.html');
        return new Response('', { status: 503 });
      }))
  );
}

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isNavigate = event.request.mode === 'navigate';

  // 页面导航 / 首页 — 网络优先，失败回落缓存首页（不打空白）
  if (isNavigate || url.pathname.endsWith('/index.html')) {
    networkFirstWithFallback(event, CACHE_NAME);
    return;
  }

  // 行情数据 (data/*.json) — 网络优先保证实时；网关 502 时回落上一份数据，宁可旧也不要打不开
  if (url.pathname.includes('/data/') && url.pathname.endsWith('.json')) {
    networkFirstWithFallback(event, DATA_CACHE);
    return;
  }

  // 其他静态资源：缓存优先
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response.ok && event.request.method === 'GET' && url.origin === location.origin) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        if (event.request.mode === 'navigate') {
          return caches.match('./index.html');
        }
      });
    })
  );
});
