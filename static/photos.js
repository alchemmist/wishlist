import {beginSpan, pageSpan} from './telemetry.js';
// Browser cache warming is deliberately gradual. This is not the upload or
// optimizer pipeline: visible covers outrank nearby galleries and idle work.
const cache = new Map(), queue = [];
let running = 0;
function pump() {
  queue.sort((a, b) => a.priority - b.priority);
  while (running < 4 && queue.length) {
    const job = queue.shift(); running++;
    const loadSpan = beginSpan('browser.image.load', performance.now() < 10_000 ? pageSpan.context : null, {route: '/media/{key}'});
    const image = new Image(); job.image = image;
    image.decoding = 'async'; image.fetchPriority = job.priority <= 0 ? 'high' : 'low';
    image.onload = async () => {
      try { const decode = beginSpan('browser.image.decode', loadSpan.context, {route: '/media/{key}'});
        try { await image.decode(); decode.end(); } catch (error) { decode.end({error: true}); throw error; }
        loadSpan.end({status: 200}); job.state = 'ready'; job.resolve(image); }
      catch { fail(); return; }
      finish();
    };
    const fail = () => { loadSpan.end({error: true}); job.state = 'error'; job.reject(new Error('Photo unavailable')); finish(); };
    const finish = () => {
      image.onload = image.onerror = null; running--;
      // Keep only a modest number of decoded previews. HTTP cache retains bytes.
      for (const [key, entry] of cache) {
        if (cache.size <= 128) break;
        if (entry.state !== 'pending') cache.delete(key);
      }
      pump();
    };
    image.onerror = fail; image.src = job.url;
  }
}
export function preloadPhoto(url, priority = 2) {
  let job = cache.get(url);
  if (job?.state === 'error') { cache.delete(url); job = null; }
  if (job) { job.priority = Math.min(job.priority, priority);
    if (job.image && job.priority <= 0) job.image.fetchPriority = 'high';
    pump(); return job.promise; }
  job = {url, priority, state: 'pending'};
  job.promise = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
  cache.set(url, job); queue.push(job); pump(); return job.promise;
}
const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) {
    entry.target.dispatchEvent(new Event('gallery-nearby')); observer.unobserve(entry.target);
  }
}, {rootMargin: '600px'}) : null;
export function warmGallery(figure, photos) {
  const warm = priority => { for (const photo of photos) preloadPhoto(photo.preview || photo.url, priority).catch(() => {}); };
  figure.addEventListener('gallery-nearby', () => warm(2), {once: true});
  observer?.observe(figure);
  const idle = window.requestIdleCallback || (callback => setTimeout(callback, 100));
  idle(() => { if (figure.isConnected) warm(3); });
}

export function forgetGallery(figure) { observer?.unobserve(figure); }
