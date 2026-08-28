{% load static static_versioning %}
const CACHE_PREFIX = "lunora-pwa";
const STATIC_CACHE = `${CACHE_PREFIX}-static-{% static_version 'css/base.css' 'css/offline.css' 'js/base.js' %}`;
const OFFLINE_URL = "{% url 'offline' %}";
const APP_SHELL_URLS = [
  OFFLINE_URL,
  "{% versioned_static 'css/base.css' %}",
  "{% versioned_static 'css/offline.css' %}",
  "{% static 'img/lunora_background.webp' %}?v=brand-3",
  "{% static 'img/lunora_logo.png' %}?v=brand-3",
  "{% static 'img/icon-192.png' %}?v=brand-3",
  "{% static 'img/icon-512.png' %}?v=brand-3",
  "{% static 'img/icon-maskable-512.png' %}?v=brand-3",
  "{% static 'img/favicon-32.png' %}?v=brand-3",
  "{% static 'img/apple-touch-icon.png' %}?v=brand-3",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(APP_SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => Promise.all(
        cacheNames
          .filter((cacheName) => cacheName.startsWith(CACHE_PREFIX) && cacheName !== STATIC_CACHE)
          .map((cacheName) => caches.delete(cacheName))
      ))
      .then(() => self.clients.claim())
  );
});

async function serveStaticAsset(request) {
  const cachedResponse = await caches.match(request);
  if (cachedResponse) return cachedResponse;

  const networkResponse = await fetch(request);
  if (networkResponse.ok) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, networkResponse.clone());
  }
  return networkResponse;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const requestUrl = new URL(request.url);
  if (requestUrl.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  if (requestUrl.pathname.startsWith("/static/")) {
    event.respondWith(serveStaticAsset(request));
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "Lunora";
  const options = {
    body: payload.body || "Du hast eine neue Benachrichtigung.",
    icon: "{% static 'img/icon-192.png' %}?v=brand-3",
    badge: "{% static 'img/favicon-32.png' %}?v=brand-3",
    tag: payload.tag || "lunora-notification",
    data: {
      url: typeof payload.url === "string" && payload.url.startsWith("/")
        ? payload.url
        : "/notifications/",
    },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const requestedPath = event.notification.data?.url || "/notifications/";
  let targetUrl = new URL("/notifications/", self.location.origin);
  try {
    const candidate = new URL(requestedPath, self.location.origin);
    if (candidate.origin === self.location.origin) targetUrl = candidate;
  } catch (error) {
    // Keep the safe notification-center fallback for malformed payload URLs.
  }

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (windowClients) => {
      const existingClient = windowClients.find((client) => new URL(client.url).origin === self.location.origin);
      if (existingClient) {
        await existingClient.focus();
        if ("navigate" in existingClient) await existingClient.navigate(targetUrl.href);
        return;
      }
      if (clients.openWindow) await clients.openWindow(targetUrl.href);
    })
  );
});
