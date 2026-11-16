import base64
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry, generate_latest
from PIL import Image

from app import telemetry
from app.main import app, db, DATABASE_PATH

@pytest.fixture
def traced(monkeypatch):
    exporter = InMemorySpanExporter(); provider = TracerProvider()
    processor = SimpleSpanProcessor(exporter); provider.add_span_processor(processor)
    monkeypatch.setattr(telemetry, 'TRACER', provider.get_tracer('test'))
    monkeypatch.setattr(telemetry, 'PROCESSOR', processor)
    db.execute("INSERT INTO users(sub, username) VALUES ('observe', 'Observe')"); db.commit()
    payload = base64.b64encode(json.dumps({'sub':'observe'}).encode())
    with TestClient(app) as client:
        client.cookies.set('session', TimestampSigner('test').sign(payload).decode(), domain='testserver.local')
        yield client, exporter
    provider.shutdown()

def test_distributed_parent_database_children_and_private_content_exclusion(traced):
    client, exporter = traced
    trace_id = 'ab'*16; parent_id = 'cd'*8
    response = client.post('/api/wishlists/Observe/items', json={'title':'SECRET-PRIVATE-TITLE','links':['https://example.com/private']}, headers={'traceparent':f'00-{trace_id}-{parent_id}-01'})
    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    server = next(span for span in spans if span.name == 'POST /api/wishlists/{username}/items')
    assert server.context.trace_id == int(trace_id,16) and server.parent.span_id == int(parent_id,16)
    assert any(span.name == 'db.commit' and span.context.trace_id == server.context.trace_id for span in spans)
    assert response.headers['traceparent'].split('-')[1] == trace_id
    rendered = ' '.join(span.to_json() for span in spans)
    assert 'SECRET-PRIVATE-TITLE' not in rendered and 'https://example.com/private' not in rendered
    assert 'db.statement' not in rendered and 'session' not in rendered

def test_inventory_authoritative_counts_and_missing_data_is_not_zero(traced, tmp_path):
    client, _ = traced
    client.post('/api/wishlists/Observe/items', json={'title':'Gift'})
    registry = CollectorRegistry(); registry.register(telemetry.Inventory(DATABASE_PATH))
    metrics = generate_latest(registry).decode()
    assert 'wishlist_users 1.0' in metrics and 'wishlist_items 1.0' in metrics
    assert 'wishlist_inventory_scrape_success 1.0' in metrics
    bad = CollectorRegistry(); bad.register(telemetry.Inventory(tmp_path/'missing.sqlite'))
    missing = generate_latest(bad).decode()
    assert 'wishlist_inventory_scrape_success 0.0' in missing
    assert 'wishlist_users ' not in missing
    assert 'Observe' not in metrics

def test_browser_relay_validated_bounded_and_preserves_original_context(traced):
    client, exporter = traced; now = time.time()*1000
    value = {'name':'browser.save','trace_id':'ab'*16,'span_id':'cd'*8,'parent_id':None,'start_ms':now-100,'end_ms':now,'route':'/other'}
    assert client.post('/api/telemetry/traces', json={'spans':[value]}).status_code == 204
    browser = next(span for span in exporter.get_finished_spans() if span.name == 'browser.save')
    assert browser.context.trace_id == int('ab'*16,16)
    assert browser.resource.attributes['service.name'] == 'wishlist-browser'
    assert client.post('/api/telemetry/traces', json={'spans':[{**value,'title':'PRIVATE'}]}).status_code == 422
    assert client.post('/api/telemetry/traces', json={'spans':[{**value,'route':'/api/wishlists/PrivateUser'}]}).status_code == 422
    assert client.post('/api/telemetry/traces', json={'spans':[value]*33}).status_code == 422
    assert client.post('/api/telemetry/traces', json={'spans':[{**value,'name':'arbitrary-PII'}]}).status_code == 422

def test_browser_budget_and_no_public_metrics(traced, monkeypatch):
    client, _ = traced
    monkeypatch.setattr('app.main.accept_browser_batch', lambda: False)
    assert client.post('/api/telemetry/traces',json={'spans':[]}).status_code == 429
    response = client.get('/metrics')
    assert 'wishlist_users' not in response.text
    assert client.post('/api/telemetry/traces',json={'spans':[]},headers={'origin':'https://evil.example'}).status_code == 403

def test_storage_validation_spans_and_metadata_edit_never_calls_storage(traced, monkeypatch):
    client, exporter = traced
    item = client.post('/api/wishlists/Observe/items',json={'title':'Gift'}).json()
    data = io.BytesIO(); Image.new('RGB',(2,2)).save(data,format='PNG')
    response = client.put(f'/api/items/{item["id"]}/images/12345678-1234-1234-1234-123456789abc', files={'file':('private-name.png',data.getvalue(),'image/png')})
    assert response.status_code == 200
    names = [span.name for span in exporter.get_finished_spans()]
    assert 'storage.put' in names and 'image.validate' in names
    from app.main import store
    monkeypatch.setattr(store, 'put_file', lambda *args: pytest.fail('Metadata edit must not upload'))
    monkeypatch.setattr(store, 'delete', lambda *args: pytest.fail('Metadata edit must not delete'))
    response = client.patch(f'/api/items/{item["id"]}',json={'title':'Changed'})
    assert response.status_code == 200 and len(response.json()['photos']) == 1
    assert 'private-name.png' not in ' '.join(span.to_json() for span in exporter.get_finished_spans())
