# Wishlist observability

## Signals and privacy

- Service `wishlist`: W3C-linked HTTP requests through response streaming;
  multipart receive seconds/bytes, SQLite connect/verb/commit/rollback,
  image validation, storage operations, S3 writes, media streaming and OIDC calls.
- `wishlist-browser`: page, Save, fetch headers/TTFB, XHR upload (through response),
  render and full-resource timing. Every Save gets a new trace ID. Native images
  cannot set traceparent; Server-Timing links their resource span to the backend
  context where supported. No cache-busting photo URLs are introduced.
- No raw routes/queries, SQL/parameters, usernames, titles/descriptions/links,
  filenames, cookies/headers, bodies or exception content. Logs use route templates
  and trace_id, replacing raw uvicorn URL/query access logs.
- Browser relay `/api/telemetry/traces`: fixed schema, max 32 spans, no arbitrary
  attributes/exporter URL, same-origin protection, normal 256KiB body cap and global
  120 batches/min sustained / 60 burst budget. Browser queue max128, SDK max2048;
  failed telemetry never blocks saves. These are not durable audit-log queues.

## Configuration

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` enables HTTP/protobuf export. Only verified
HTTPS is accepted. Native SDK environment variables select the CA, client cert
and key (`OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE`, `...CLIENT_CERTIFICATE`,
`...CLIENT_KEY`). No new public Collector endpoint and no insecure TLS bypass.
Without exporter config, local tests use no network exporter. SDK source sampling
is AlwaysOn; the existing central sampler decides retention, not this app.

`METRICS_PORT=8001` starts a separate private HTTP listener, **not a public app
route**. The chart exposes it only on the ClusterIP service, never the ingress.
Do not port-forward/publish this listener to the internet. One application worker
is required by the current SQLite/singleton deployment and process-local counters.

## Metrics

- `wishlist_http_requests_total{method,route,status}` and
  `wishlist_http_request_duration_seconds`: complete request/stream duration.
- `wishlist_operation_duration_seconds{operation,outcome}`: DB/storage/validation.
- `wishlist_users`, `wishlist_guests`, `wishlist_items`, `wishlist_item_images`,
  `wishlist_links`, `wishlist_reservations`: current authoritative DB counts, not
  unique visitors, active sessions or lifetime deleted entities.
- `wishlist_inventory_scrape_success`: failed DB collection omits counts rather
  than emitting false zeroes. Counter/histogram history resets on process restart.
- `wishlist_browser_telemetry_rejected_total`: rate-limited browser batches.

## Production analysis

Homelab chart owns private metrics scraping through the existing native Collector
and OTLP pipeline; existing Prometheus/Thanos, Tempo and Loki datasources are used.
Dashboard: `https://grafana.socrat.alchemmist.xyz/d/wishlist-performance` (private
Grafana access required). Compare browser.resource full transfer, HTTP receive,
image.validate, s3.put and db.commit; do not attribute transfer delay to S3 solely
from a spinner or one HEAD request. Fetch spans alone are TTFB, not full-body time.

Existing tail policy: decide after30s, retain errors/5xx and >=2s traces, sample
normal traffic10%. Very late spans can miss an earlier decision; a healthy
exporter/Argo state does not prove delivery or complete trace coverage. Native
OS clipboard access and production user Authentik sessions are not exercised by
fixture-based browser tests.
