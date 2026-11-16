// Content-free W3C spans. No cookies, URLs, titles, filenames or bodies leave here.
const nativeFetch = globalThis.fetch.bind(globalThis);
const queue = [], requests = new Map();
let flushTimer;
const hex = bytes => [...crypto.getRandomValues(new Uint8Array(bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
export function routeOf(input) {
  const path = new URL(input, location.origin).pathname;
  if (/^\/api\/wishlists\/[^/]+\/items$/.test(path)) return '/api/wishlists/{username}/items';
  if (/^\/api\/wishlists\/[^/]+$/.test(path)) return '/api/wishlists/{username}';
  if (/^\/api\/items\/\d+\/images\/[^/]+$/.test(path)) return '/api/items/{item_id}/images/{photo_id}';
  if (/^\/api\/items\/\d+\/(images|reserve|release)$/.test(path)) return path.replace(/\/\d+\//, '/{item_id}/');
  if (/^\/api\/items\/\d+$/.test(path)) return '/api/items/{item_id}';
  if (path === '/api/me') return path;
  if (path.startsWith('/media/')) return '/media/{key}';
  if (path.startsWith('/static/')) return '/static/{path}';
  return '/other';
}
export function beginSpan(name, parent = null, attrs = {}, start = performance.timeOrigin + performance.now()) {
  const value = {name, trace_id: parent?.trace_id || hex(16), span_id: hex(8), parent_id: parent?.span_id || null,
    start_ms: start, route: '/other', method: 'OTHER', status: 0, error: false, ...attrs};
  let ended = false;
  return {context: value, end(extra = {}, end = performance.timeOrigin + performance.now()) {
    if (ended) return; ended = true;
    if (queue.length < 128) queue.push({...value, ...extra, end_ms: end});
    if (!flushTimer) flushTimer = setTimeout(flush, 2000);
  }};
}
export const pageSpan = beginSpan('browser.page', null, {route: location.pathname === '/' ? '/' : '/{username}'}, performance.timeOrigin);
export const traceHeader = context => '00-' + context.trace_id + '-' + context.span_id + '-01';
export function rememberRequest(url, context) {
  if (requests.size >= 128) requests.delete(requests.keys().next().value);
  requests.set(new URL(url, location.origin).href, context);
}
export async function tracedFetch(input, options = {}, parent = performance.now() < 10_000 ? pageSpan.context : null) {
  const url = new URL(input, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/') || url.pathname === '/api/telemetry/traces') return nativeFetch(input, options);
  const span = beginSpan('browser.fetch', parent, {route: routeOf(input), method: (options.method || 'GET').toUpperCase()});
  const headers = new Headers(options.headers); headers.set('traceparent', traceHeader(span.context)); rememberRequest(input, parent || span.context);
  try {
    const response = await nativeFetch(input, {...options, headers});
    span.end({status: response.status, error: response.status >= 400}); return response;
  } catch (error) { span.end({error: true}); throw error; }
}
function flush() {
  clearTimeout(flushTimer); flushTimer = null;
  if (!queue.length) return;
  const body = JSON.stringify({spans: queue.splice(0, 32)});
  nativeFetch('/api/telemetry/traces', {method: 'POST', headers: {'content-type': 'application/json'}, body, keepalive: true}).catch(() => {});
  if (queue.length) flushTimer = setTimeout(flush, 2000);
}
addEventListener('pagehide', () => { if (queue.length) flush(); });
addEventListener('load', () => pageSpan.end(), {once: true});
if (document.readyState === 'complete') pageSpan.end();
if (globalThis.PerformanceObserver) {
  const observer = new PerformanceObserver(list => {
    for (const entry of list.getEntries()) {
      const url = new URL(entry.name, location.origin);
      if (url.origin !== location.origin || url.pathname === '/api/telemetry/traces') continue;
      const route = routeOf(url.href); if (route === '/other') continue;
      const parent = requests.get(url.href) || (performance.now() < 10_000 ? pageSpan.context : null);
      const span = beginSpan('browser.resource', parent, {route}, performance.timeOrigin + entry.startTime);
      const linked = entry.serverTiming?.find(timing => timing.name === 'traceparent')?.description?.match(/^00-([0-9a-f]{32})-([0-9a-f]{16})-/);
      span.end(linked ? {link_trace_id: linked[1], link_span_id: linked[2]} : {}, performance.timeOrigin + entry.startTime + entry.duration);
    }
  });
  observer.observe({type: 'resource', buffered: true});
}
