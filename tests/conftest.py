import os
import tempfile

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='wishlist-tests-')
os.environ['SESSION_SECRET'] = 'test'
os.environ['PUBLIC_URL'] = 'http://localhost'
for name in ('IMAGE_OPTIMIZER_URL', 'S3_ENDPOINT', 'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'S3_PUBLIC_BASE', 'OIDC_ISSUER', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET'):
    os.environ[name] = ''

import pytest


@pytest.fixture(autouse=True)
def clean_database():
    from app.main import db
    for table in ('reservations', 'links', 'items', 'guests', 'users'):
        db.execute(f'DELETE FROM {table}')
    db.commit()
    yield
