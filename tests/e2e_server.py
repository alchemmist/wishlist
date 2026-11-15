"""Isolated E2E application + OIDC fixture. Never imported by production code."""
import base64
import hashlib
import html
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from joserfc import jwt
from joserfc.jwk import RSAKey
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

PORT = int(os.environ.get("E2E_PORT", "19421"))
ISSUER = f"http://127.0.0.1:{PORT + 1}"
PUBLIC = f"http://127.0.0.1:{PORT}"
provider = FastAPI()
key = RSAKey.generate_key(2048, parameters={"kid": "e2e-key"})
bad_key = RSAKey.generate_key(2048, parameters={"kid": "bad-key"})
codes = {}
tokens = {}


@provider.get('/.well-known/openid-configuration')
def metadata():
    return {
        'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
        'token_endpoint': ISSUER + '/token', 'userinfo_endpoint': ISSUER + '/userinfo',
        'jwks_uri': ISSUER + '/jwks', 'response_types_supported': ['code'],
        'id_token_signing_alg_values_supported': ['RS256'],
        'token_endpoint_auth_methods_supported': ['client_secret_basic'],
        'code_challenge_methods_supported': ['S256'],
    }


@provider.get('/jwks')
def jwks():
    return {'keys': [key.as_dict(private=False)]}


@provider.get('/authorize')
def authorize(request: Request):
    params = dict(request.query_params)
    assert params['client_id'] == 'e2e-client'
    assert params['redirect_uri'] == PUBLIC + '/login/callback'
    assert params['code_challenge_method'] == 'S256'
    assert params['nonce'] and params['state'] and params['code_challenge']
    inputs = ''.join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in params.items())
    return HTMLResponse(f'<!doctype html><html lang="en"><title>Test identity provider</title><h1>Test identity provider</h1><form action="/approve">{inputs}<label>Username<input name="username" required></label><button>Continue</button></form></html>')


@provider.get('/approve')
def approve(request: Request):
    params = dict(request.query_params)
    code = secrets.token_urlsafe(24)
    codes[code] = params
    return RedirectResponse(params['redirect_uri'] + '?' + urlencode({'code': code, 'state': params['state']}))


@provider.post('/token')
async def token(request: Request):
    expected = 'Basic ' + base64.b64encode(b'e2e-client:e2e-secret').decode()
    if request.headers.get('authorization') != expected:
        raise HTTPException(401)
    form = await request.form()
    params = codes.pop(form.get('code'), None)
    if not params or form.get('redirect_uri') != params['redirect_uri']:
        raise HTTPException(400)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get('code_verifier', '').encode()).digest()).rstrip(b'=').decode()
    if challenge != params['code_challenge']:
        raise HTTPException(400, 'PKCE mismatch')
    username = params['username']
    access = secrets.token_urlsafe(24)
    info = {'sub': 'e2e-' + username, 'preferred_username': username, 'name': username}
    tokens[access] = info
    now = int(time.time())
    claims = {'iss': ISSUER, 'aud': 'e2e-client', 'sub': info['sub'], 'iat': now, 'exp': now + 300, 'nonce': params['nonce']}
    signing_key = key
    if username == 'bad-signature':
        signing_key = bad_key
    elif username == 'bad-nonce':
        claims['nonce'] = 'wrong'
    elif username == 'bad-audience':
        claims['aud'] = 'other-client'
    elif username == 'bad-issuer':
        claims['iss'] = 'https://other.invalid'
    elif username == 'expired-token':
        claims['exp'] = now - 600
    encoded = jwt.encode({'alg': 'RS256', 'kid': key.as_dict()['kid']}, claims, signing_key)
    return {'access_token': access, 'token_type': 'Bearer', 'expires_in': 300, 'id_token': encoded}


@provider.get('/userinfo')
def userinfo(request: Request):
    info = tokens.get(request.headers.get('authorization', '').removeprefix('Bearer '))
    if not info:
        raise HTTPException(401)
    if info['preferred_username'] == 'bad-subject':
        return {**info, 'sub': 'other-subject'}
    return info


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='wishlist-e2e-') as data:
        os.environ.update(DATA_DIR=data, SESSION_SECRET='e2e-only-not-for-production', PUBLIC_URL=PUBLIC,
                          OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID='e2e-client', OIDC_CLIENT_SECRET='e2e-secret',
                          S3_ENDPOINT='', S3_ACCESS_KEY='', S3_SECRET_KEY='', S3_PUBLIC_BASE='')
        from app.main import app
        server = uvicorn.Server(uvicorn.Config(provider, host='127.0.0.1', port=PORT + 1, log_level='warning'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError(f"Test OIDC provider could not start on {ISSUER}")
            time.sleep(.02)
        try:
            uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')
        finally:
            server.should_exit = True
            thread.join(timeout=5)
