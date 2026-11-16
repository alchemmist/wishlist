"""Bounded, content-free OTel spans; private pull metrics, never a public OTLP proxy."""
import functools
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager, closing
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanContext, TraceFlags, SpanKind, Status, StatusCode, Link
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram, start_http_server
from prometheus_client.core import GaugeMetricFamily

RESOURCE = Resource.create({'service.name': 'wishlist', 'service.version': os.environ.get('APP_VERSION', 'unknown')})
PROVIDER = TracerProvider(resource=RESOURCE)
PROCESSOR = None
endpoint = os.environ.get('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', '')
if endpoint:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    if not endpoint.startswith('https://'):
        raise RuntimeError('Production trace export requires verified HTTPS')
    PROCESSOR = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5), max_queue_size=2048, max_export_batch_size=128, schedule_delay_millis=1000)
    PROVIDER.add_span_processor(PROCESSOR)
TRACER = PROVIDER.get_tracer('wishlist')
PROPAGATOR = TraceContextTextMapPropagator()
REGISTRY = CollectorRegistry()
REQUESTS = Counter('wishlist_http_requests_total', 'Completed HTTP requests', ['method', 'route', 'status'], registry=REGISTRY)
DURATION = Histogram('wishlist_http_request_duration_seconds', 'Full HTTP response duration', ['method', 'route'], registry=REGISTRY, buckets=(.005,.01,.025,.05,.1,.25,.5,1,2,5,10,30,60))
OPERATIONS = Histogram('wishlist_operation_duration_seconds', 'Database, validation and storage operation duration', ['operation', 'outcome'], registry=REGISTRY, buckets=(.001,.005,.01,.025,.05,.1,.25,.5,1,2,5,10,30,60))
BROWSER_DROPPED = Counter('wishlist_browser_telemetry_rejected_total', 'Rate-limited browser batches', registry=REGISTRY)
LOG = logging.getLogger('wishlist.telemetry')
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    LOG.addHandler(logging.StreamHandler())

@contextmanager
def operation(name):
    started = time.perf_counter(); outcome = 'ok'
    with TRACER.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except Exception as exc:
            outcome = 'error'; span.set_attribute('error.type', type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR)); raise
        finally:
            OPERATIONS.labels(name, outcome).observe(time.perf_counter() - started)

def measured(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with operation(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate

class TracedConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        verb = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else 'OTHER'
        if verb not in {'SELECT','INSERT','UPDATE','DELETE','BEGIN','PRAGMA','CREATE','ALTER'}: verb = 'OTHER'
        with operation('db.' + verb.lower()):
            return super().execute(sql, parameters)
    @measured('db.commit')
    def commit(self): return super().commit()
    @measured('db.rollback')
    def rollback(self): return super().rollback()

class Inventory:
    def __init__(self, database): self.database = database
    def collect(self):
        success = GaugeMetricFamily('wishlist_inventory_scrape_success', 'Whether authoritative inventory counts were read')
        counts = []
        try:
            from app.db import normalize
            with closing(sqlite3.connect(f'file:{self.database}?mode=ro', uri=True, timeout=.2)) as db:
                db.create_collation('UNICODE_NOCASE', lambda a, b: (normalize(a) > normalize(b)) - (normalize(a) < normalize(b)))
                for table in ('users','guests','items','item_images','links','reservations'):
                    counts.append((table, db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]))
            success.add_metric([], 1)
        except sqlite3.Error:
            success.add_metric([], 0)
        yield success
        for table, count in counts if len(counts) == 6 else []:
            gauge = GaugeMetricFamily('wishlist_' + table, 'Current authoritative ' + table + ' count')
            gauge.add_metric([], count); yield gauge

class RequestTelemetry:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return await self.app(scope, receive, send)
        headers = {key.decode('latin1'): value.decode('latin1') for key, value in scope['headers'] if key == b'traceparent'}
        started = time.perf_counter(); status = 500; received = 0; upload_finished = None
        method = scope['method'] if scope['method'] in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} else 'OTHER'
        with TRACER.start_as_current_span('HTTP', context=PROPAGATOR.extract(headers), kind=SpanKind.SERVER, record_exception=False, set_status_on_exception=False) as span:
            async def traced_receive():
                nonlocal received, upload_finished
                message = await receive()
                if message['type'] == 'http.request':
                    received += len(message.get('body', b''))
                    if not message.get('more_body'): upload_finished = time.perf_counter() - started
                return message
            async def traced_send(message):
                nonlocal status
                if message['type'] == 'http.response.start':
                    status = message['status']; carrier = {}; PROPAGATOR.inject(carrier)
                    message = dict(message); message['headers'] = list(message.get('headers', [])) + [(b'traceparent', carrier.get('traceparent', '').encode()), (b'server-timing', ('app;dur=%.2f, traceparent;desc="%s"' % ((time.perf_counter()-started)*1000, carrier.get('traceparent',''))).encode())]
                await send(message)
            try:
                await self.app(scope, traced_receive, traced_send)
            except Exception as exc:
                span.set_attribute('error.type', type(exc).__name__); span.set_status(Status(StatusCode.ERROR)); raise
            finally:
                route = getattr(scope.get('route'), 'path', '/unmatched')
                if route == '/unmatched' and scope.get('path','').startswith('/static/'): route = '/static/{path}'
                elapsed = time.perf_counter() - started
                span.update_name(method + ' ' + route)
                span.set_attributes({'http.request.method': method, 'http.route': route, 'http.response.status_code': status, 'http.request.body.size': received})
                if upload_finished is not None: span.set_attribute('wishlist.request_body_receive_seconds', upload_finished)
                if status >= 500: span.set_status(Status(StatusCode.ERROR))
                REQUESTS.labels(method, route, str(status)).inc(); DURATION.labels(method, route).observe(elapsed)
                LOG.info('method=%s route=%s status=%s duration_ms=%.1f trace_id=%032x', method, route, status, elapsed*1000, span.get_span_context().trace_id)

# Global bounded budget, not IP-labelled: no unbounded per-client state/cardinality.
_budget_lock = threading.Lock(); _tokens = 60.; _last = time.monotonic()
def accept_browser_batch():
    global _tokens, _last
    with _budget_lock:
        now = time.monotonic(); _tokens = min(60., _tokens + (now - _last)*2); _last = now
        if _tokens < 1: BROWSER_DROPPED.inc(); return False
        _tokens -= 1; return True

def ingest_browser(spans):
    if PROCESSOR is None: return
    resource = Resource.create({'service.name': 'wishlist-browser'})
    now = time.time_ns()
    for value in spans:
        start = int(value.start_ms * 1_000_000); end = int(value.end_ms * 1_000_000)
        if not (now - 3_600_000_000_000 <= start <= end <= now + 60_000_000_000): continue
        context = SpanContext(int(value.trace_id,16), int(value.span_id,16), False, TraceFlags(1))
        if not context.is_valid: continue
        parent = SpanContext(context.trace_id, int(value.parent_id,16), True, TraceFlags(1)) if value.parent_id else None
        attrs = {'http.route': value.route, 'http.request.method': value.method, 'http.response.status_code': value.status}
        links = []
        if value.link_trace_id and value.link_span_id:
            linked = SpanContext(int(value.link_trace_id,16), int(value.link_span_id,16), True, TraceFlags(1))
            if linked.is_valid: links.append(Link(linked))
        span = ReadableSpan(value.name, links=links, context=context, parent=parent, resource=resource, attributes=attrs,
                            start_time=start, end_time=end, status=Status(StatusCode.ERROR if value.error else StatusCode.UNSET))
        PROCESSOR.on_end(span)

def start_metrics(database):
    port = int(os.environ.get('METRICS_PORT', '0'))
    if not port: return None
    registry = CollectorRegistry(); registry.register(Inventory(database))
    # Existing counters are registered once; private listener never mounts on public app.
    for collector in (REQUESTS,DURATION,OPERATIONS,BROWSER_DROPPED): registry.register(collector)
    return start_http_server(port, addr='0.0.0.0', registry=registry)
