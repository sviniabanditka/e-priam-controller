// Minimal service worker: makes the page installable and loads offline.
const CACHE = "epriam-v2";
const FILES = ["./", "./index.html", "./priam_ble.js", "./manifest.json", "./icon.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES))));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => e.respondWith(
  fetch(e.request).then((r) => { caches.open(CACHE).then((c) => c.put(e.request, r.clone())); return r; })
    .catch(() => caches.match(e.request))));
