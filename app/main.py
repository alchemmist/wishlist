import html
import hashlib
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from urllib.parse import urlsplit, quote

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from app.telemetry import RequestTelemetry, start_metrics, PROVIDER, ingest_browser, accept_browser_batch, operation, measured, TRACER, OPERATIONS
from app.db import connect, guest
from app.storage import Store
from app.middleware import RequestBodyLimitMiddleware
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from joserfc.errors import JoseError

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://wishlist.alchemmist.xyz").rstrip("/")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    if PUBLIC_URL.startswith("https://"):
        raise RuntimeError("SESSION_SECRET must be set for an HTTPS deployment")
    SESSION_SECRET = secrets.token_hex(32)

DATABASE_PATH = DATA / "wishlist.sqlite"

@asynccontextmanager
async def lifespan(app):
    logging.getLogger("uvicorn.access").disabled = True  # Sanitized, trace-correlated summaries replace raw URL/query logs.
    metrics = start_metrics(DATABASE_PATH)
    try:
        yield
    finally:
        if metrics:
            await run_in_threadpool(metrics[0].shutdown)
            metrics[0].server_close()
        await run_in_threadpool(PROVIDER.force_flush, 1000)

app = FastAPI(lifespan=lifespan)
# Initialize/migrate once, not on every request. Routes use isolated connections.
db = connect(DATABASE_PATH)


def get_db():
    connection = connect(DATABASE_PATH, initialize=False)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


logger = logging.getLogger(__name__)
store = Store(
    DATA,
    os.environ.get("S3_ENDPOINT", ""),
    os.environ.get("S3_BUCKET", "wishlist"),
    os.environ.get("S3_ACCESS_KEY", ""),
    os.environ.get("S3_SECRET_KEY", ""),
    os.environ.get("S3_PUBLIC_BASE", ""),
)


class ItemIn(BaseModel):
    request_id: UUID | None = None
    title: str = Field(max_length=120)
    description: str = Field(default="", max_length=400)
    price: str = Field(default="", max_length=40)
    links: list[Annotated[str, StringConstraints(max_length=2048)]] = Field(default_factory=list, max_length=20)


class NameIn(BaseModel):
    name: str = Field(max_length=60)


def session_user(request: Request, db):
    sub = request.session.get("sub")
    if not sub:
        return None
    return db.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()


def require_owner(request: Request, owner_id: int, db):
    user = session_user(request, db)
    if not user:
        raise HTTPException(401, "Sign in required")
    if user["id"] != owner_id:
        raise HTTPException(403, "This is not your wishlist")
    return user


def links_of(item_id: int, db) -> list[str]:
    return [row["url"] for row in db.execute("SELECT url FROM links WHERE item_id = ? ORDER BY id", (item_id,))]


def photos_of(item_id, db):
    return [{"id": row["id"], "url": store.url(row["image_key"]), "preview": store.preview_url(row["image_key"])} for row in
            db.execute("SELECT * FROM item_images WHERE item_id = ? ORDER BY position, id", (item_id,))]


def item_json(row, mine: bool, db, links=None, reserved=None) -> dict:
    if reserved is None:
        reserved = db.execute("SELECT 1 FROM reservations WHERE item_id = ?", (row["id"],)).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "price": row["price"],
        "image": store.url(row["image_key"]),
        "photos": photos_of(row["id"], db),
        "links": links_of(row["id"], db) if links is None else links,
        "reserved": bool(reserved),
        "mine": mine,
    }


def safe_links(values: list[str]) -> list[str]:
    clean = []
    for value in values:
        value = value.strip()
        try:
            parsed = urlsplit(value)
            parsed.port  # Validate port syntax and range too.
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in value))
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(422, "Links must be valid HTTP or HTTPS URLs")
        if value not in clean:
            clean.append(value)
    return clean


from starlette.middleware.sessions import SessionMiddleware

app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=PUBLIC_URL.startswith("https://"),
    same_site="lax",
)


@app.exception_handler(HTTPException)
async def friendly_login_errors(request: Request, exc: HTTPException):
    if request.url.path.startswith("/login"):
        message = html.escape(str(exc.detail))
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Sign-in / Wishlist</title><link rel="stylesheet" href="/static/style.css">'
            f'<main><h1>Sign-in</h1><p role="alert">{message}</p>'
            '<p><a href="/login">Try signing in again</a> · <a href="/">Back to Wishlist</a></p></main></html>',
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(sqlite3.OperationalError)
async def database_unavailable(request: Request, exc: sqlite3.OperationalError):
    logger.error("Database operation failed", exc_info=exc)
    response = await friendly_login_errors(request, HTTPException(503, "Database is temporarily unavailable. Please try again."))
    response.headers["Retry-After"] = "1"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if (origin and origin.rstrip("/") != PUBLIC_URL) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if not request.url.path.startswith(("/static/", "/media/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/me")
def me(request: Request, db=Depends(get_db)):
    user = session_user(request, db)
    if not user:
        return {"user": None}
    return {"user": {"username": user["username"], "name": user["display_name"], "avatar": user["avatar"]}}


oauth = OAuth()
oauth.register(
    "authentik",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
    client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
)


def valid_username(value: str) -> bool:
    return (
        bool(value) and len(value) <= 150
        and not any(c.isspace() or ord(c) < 32 or c in "/\\?#%" for c in value)
        and value.casefold() not in {".", "..", "api", "login", "logout", "media", "static", "docs", "redoc", "openapi.json"}
    )


@app.get("/login")
async def login(request: Request):
    if not (ISSUER and CLIENT_ID and CLIENT_SECRET):
        raise HTTPException(503, "Sign-in is not configured")
    try:
        with operation("auth.redirect"):
            return await oauth.authentik.authorize_redirect(request, f"{PUBLIC_URL}/login/callback")
    except (httpx.HTTPError, OAuthError, ValueError, KeyError) as exc:
        logger.warning("Could not start OIDC login error_type=%s", type(exc).__name__)
        raise HTTPException(502, "Sign-in provider is unavailable. Please try again.")


@app.get("/login/callback")
async def callback(request: Request, db=Depends(get_db)):
    if not (ISSUER and CLIENT_ID and CLIENT_SECRET):
        raise HTTPException(503, "Sign-in is not configured")
    try:
        # Authlib checks state, PKCE, nonce, issuer, audience, expiry and JWT signature.
        with operation("auth.token_exchange"):
            token = await oauth.authentik.authorize_access_token(request)
        identity = token.get("userinfo")
        if not identity or not identity.get("sub"):
            raise ValueError("Missing verified ID token")
        metadata = await oauth.authentik.load_server_metadata()
        with operation("auth.userinfo"):
            info = await oauth.authentik.get(metadata["userinfo_endpoint"], token=token)
        info.raise_for_status()
        info = info.json()
        if not isinstance(info, dict):
            raise ValueError("Invalid userinfo response")
        if info.get("sub") != identity["sub"]:
            raise ValueError("Userinfo subject mismatch")
    except (httpx.HTTPError, OAuthError, JoseError, ValueError, KeyError, TypeError) as exc:
        logger.warning("OIDC callback rejected (%s)", type(exc).__name__)
        raise HTTPException(400, "Sign-in failed. Please start sign-in again.")
    username = info.get("preferred_username") or info.get("nickname") or ""
    username = username.strip() if isinstance(username, str) else ""
    if not valid_username(username):
        raise HTTPException(400, "Sign-in provider returned an invalid username")
    def save_identity():
        try:
            db.execute(
                """INSERT INTO users(sub, username, display_name, avatar) VALUES (?, ?, ?, ?)
                   ON CONFLICT(sub) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, avatar=excluded.avatar""",
                (info["sub"], username, info.get("name") if isinstance(info.get("name"), str) else username, safe_avatar(info.get("picture") or info.get("avatar") or "")),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            raise HTTPException(409, "This username already belongs to another account")
    await run_in_threadpool(save_identity)
    request.session.clear()
    request.session["sub"] = info["sub"]
    return RedirectResponse(f"/{quote(username, safe='')}", status_code=303)


def safe_avatar(value):
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return ""


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/wishlists/{username}")
def wishlist(username: str, request: Request, db=Depends(get_db)):
    owner = db.execute("SELECT * FROM users WHERE username = ? COLLATE UNICODE_NOCASE", (username,)).fetchone()
    if not owner:
        raise HTTPException(404, "No wishlist")
    viewer = session_user(request, db)
    mine = bool(viewer and viewer["id"] == owner["id"])
    rows = db.execute("SELECT items.*, EXISTS(SELECT 1 FROM reservations WHERE item_id = items.id) AS is_reserved FROM items WHERE owner_id = ? ORDER BY position, id", (owner["id"],)).fetchall()
    item_links = {}
    for link in db.execute("SELECT links.item_id, links.url FROM links JOIN items ON items.id = links.item_id WHERE items.owner_id = ? ORDER BY links.id", (owner["id"],)):
        item_links.setdefault(link["item_id"], []).append(link["url"])
    return {
        "owner": {"username": owner["username"], "name": owner["display_name"], "avatar": owner["avatar"]},
        "mine": mine,
        "items": [item_json(row, mine, db, item_links.get(row["id"], []), row["is_reserved"]) for row in rows],
    }


@app.post("/api/wishlists/{username}/items")
def add_item(username: str, body: ItemIn, request: Request, db=Depends(get_db)):
    owner = db.execute("SELECT id FROM users WHERE username = ? COLLATE UNICODE_NOCASE", (username,)).fetchone()
    if not owner:
        raise HTTPException(404, "No wishlist")
    user = require_owner(request, owner["id"], db)
    urls = safe_links(body.links)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    cursor = db.execute(
        """INSERT INTO items(owner_id, title, description, price, position, creation_key)
           VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(position),0)+1 FROM items WHERE owner_id = ?), ?)
           ON CONFLICT(owner_id, creation_key) DO UPDATE SET title=excluded.title, description=excluded.description, price=excluded.price
           RETURNING id""",
        (user["id"], title, body.description.strip(), body.price.strip(), user["id"], str(body.request_id) if body.request_id else None),
    )
    item_id = cursor.fetchone()["id"]
    cursor.close()
    db.execute("DELETE FROM links WHERE item_id = ?", (item_id,))
    for url in urls:
        db.execute("INSERT INTO links(item_id, url) VALUES (?, ?)", (item_id, url))
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    result = item_json(row, True, db)
    db.commit()
    return result


@app.patch("/api/items/{item_id}")
@measured("item.update")
def edit_item(item_id: int, body: ItemIn, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT items.*, users.username FROM items JOIN users ON users.id = items.owner_id WHERE items.id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    urls = safe_links(body.links)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    db.execute("UPDATE items SET title = ?, description = ?, price = ? WHERE id = ?", (title, body.description.strip(), body.price.strip(), item_id))
    db.execute("DELETE FROM links WHERE item_id = ?", (item_id,))
    for url in urls:
        db.execute("INSERT INTO links(item_id, url) VALUES (?, ?)", (item_id, url))
    updated = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    result = item_json(updated, True, db)
    db.commit()
    return result


@app.post("/api/items/{item_id}/image")
def upload(item_id: int, request: Request, file: UploadFile = File(...), db=Depends(get_db)):
    row = db.execute("SELECT items.*, users.username FROM items JOIN users ON users.id = items.owner_id WHERE items.id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    if file.size == 0:
        raise HTTPException(400, "Choose a non-empty image")
    try:
        key = store.put_file(str(row["owner_id"]), file.file, file.content_type or "application/octet-stream")
    except ValueError as exc:
        raise HTTPException(415, str(exc))
    except Exception:
        raise HTTPException(503, "Photo storage is unavailable")
    def attach_image():
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT image_key FROM items WHERE id = ?", (item_id,)).fetchone()
            if not previous:
                raise HTTPException(404, "Missing item")
            obsolete = db.execute("SELECT image_key FROM item_images WHERE item_id = ?", (item_id,)).fetchall()
            db.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))
            db.execute("INSERT INTO item_images(id, item_id, image_key) VALUES (?, ?, ?)", (str(uuid4()), item_id, key))
            db.execute("UPDATE items SET image_key = ? WHERE id = ?", (key, item_id))
            db.commit()
        except Exception:
            db.rollback()
            store.delete(key)
            raise
        for photo in obsolete:
            store.delete(photo["image_key"])
    attach_image()
    return {"image": store.url(key)}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    obsolete = db.execute("SELECT image_key FROM item_images WHERE item_id = ?", (item_id,)).fetchall()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    for photo in obsolete:
        store.delete(photo["image_key"])
    return Response(status_code=204)


@app.delete("/api/items/{item_id}/image")
def delete_image(item_id: int, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    obsolete = db.execute("SELECT image_key FROM item_images WHERE item_id = ?", (item_id,)).fetchall()
    db.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))
    db.execute("UPDATE items SET image_key = NULL WHERE id = ?", (item_id,))
    db.commit()
    for photo in obsolete:
        store.delete(photo["image_key"])
    return {"image": None}


class PhotoOrder(BaseModel):
    ids: list[Annotated[str, StringConstraints(max_length=80)]]


def photo_owner(item_id, request, db):
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    return row


@app.put("/api/items/{item_id}/images/{photo_id}")
def add_photo(item_id: int, photo_id: UUID, request: Request, file: UploadFile = File(...), db=Depends(get_db)):
    row = photo_owner(item_id, request, db)
    photo_id = str(photo_id)
    existing = db.execute("SELECT * FROM item_images WHERE id = ?", (photo_id,)).fetchone()
    if existing:
        if existing["item_id"] != item_id:
            raise HTTPException(409, "Photo identifier already in use")
        return {"id": photo_id, "url": store.url(existing["image_key"]), "preview": store.preview_url(existing["image_key"])}
    if file.size == 0:
        raise HTTPException(400, "Choose a non-empty image")
    try:
        key = store.put_file(str(row["owner_id"]), file.file, file.content_type or "application/octet-stream")
    except ValueError as exc:
        raise HTTPException(415, str(exc))
    except Exception:
        raise HTTPException(503, "Photo storage is unavailable")
    try:
        db.execute("BEGIN IMMEDIATE")
        photo_owner(item_id, request, db)
        existing = db.execute("SELECT * FROM item_images WHERE id = ?", (photo_id,)).fetchone()
        if existing:
            if existing["item_id"] != item_id:
                raise HTTPException(409, "Photo identifier already in use")
            db.rollback()
            store.delete(key)
            return {"id": photo_id, "url": store.url(existing["image_key"]), "preview": store.preview_url(existing["image_key"])}
        count = db.execute("SELECT COUNT(*) FROM item_images WHERE item_id = ?", (item_id,)).fetchone()[0]
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM item_images WHERE item_id = ?", (item_id,)).fetchone()[0]
        db.execute("INSERT INTO item_images VALUES (?, ?, ?, ?)", (photo_id, item_id, key, position))
        if not count:
            db.execute("UPDATE items SET image_key = ? WHERE id = ?", (key, item_id))
        db.commit()
    except Exception:
        db.rollback()
        store.delete(key)
        raise
    return {"id": photo_id, "url": store.url(key), "preview": store.preview_url(key)}


@app.patch("/api/items/{item_id}/images")
@measured("gallery.update")
def save_photos(item_id: int, body: PhotoOrder, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    photo_owner(item_id, request, db)
    photos = {row["id"]: row["image_key"] for row in db.execute("SELECT * FROM item_images WHERE item_id = ?", (item_id,))}
    if len(set(body.ids)) != len(body.ids) or any(photo_id not in photos for photo_id in body.ids):
        raise HTTPException(422, "Invalid photo order")
    removed = set(photos) - set(body.ids)
    for photo_id in removed:
        db.execute("DELETE FROM item_images WHERE id = ? AND item_id = ?", (photo_id, item_id))
    for position, photo_id in enumerate(body.ids):
        db.execute("UPDATE item_images SET position = ? WHERE id = ? AND item_id = ?", (position, photo_id, item_id))
    db.execute("UPDATE items SET image_key = ? WHERE id = ?", (photos[body.ids[0]] if body.ids else None, item_id))
    result = photos_of(item_id, db)
    db.commit()
    for photo_id in removed:
        store.delete(photos[photo_id])
    return {"photos": result}


def reservation_name(request: Request, body: NameIn | None, db):
    user = session_user(request, db)
    if user:
        return user["username"]
    if body is None:
        raise HTTPException(422, "Enter a name")
    return body.name


@app.post("/api/items/{item_id}/reserve")
def reserve(item_id: int, request: Request, body: NameIn | None = None, db=Depends(get_db)):
    # Serialize the read/insert decision across connections and processes.
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    try:
        person = guest(db, reservation_name(request, body, db))
    except ValueError:
        raise HTTPException(400, "Enter a name")
    current = db.execute("SELECT guest_id FROM reservations WHERE item_id = ?", (item_id,)).fetchone()
    if current and current["guest_id"] != person["id"]:
        raise HTTPException(409, "Already reserved")
    if not current:
        db.execute("INSERT INTO reservations(item_id, guest_id) VALUES (?, ?)", (item_id, person["id"]))
    db.commit()
    return {"reserved": True, "name": person["name"]}


@app.post("/api/items/{item_id}/release")
def release(item_id: int, request: Request, body: NameIn | None = None, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    if not db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone():
        raise HTTPException(404, "Missing item")
    cleaned = " ".join(reservation_name(request, body, db).split())
    person = db.execute("SELECT id, name FROM guests WHERE name = ? COLLATE UNICODE_NOCASE", (cleaned,)).fetchone()
    current = db.execute("SELECT guest_id FROM reservations WHERE item_id = ?", (item_id,)).fetchone()
    if not person or not current or current["guest_id"] != person["id"]:
        raise HTTPException(403, "Name does not match")
    db.execute("DELETE FROM reservations WHERE item_id = ? AND guest_id = ?", (item_id, person["id"]))
    db.commit()
    return {"reserved": False}


@app.get("/media/{key:path}")
def media(key: str, request: Request, preview: bool = False):
    if not store._safe_key(key) or key.startswith('_previews/'):
        raise HTTPException(404)
    try:
        path = store.media_file(key, preview)
    except Exception:
        logger.warning("Photo preparation failed")
        raise HTTPException(503, "Photo is temporarily unavailable", headers={"Cache-Control": "no-store", "Retry-After": "2"})
    if path is None:
        raise HTTPException(404)
    variant = 'preview-v1:' if preview and store.optimizer else 'master:'
    etag = '"' + hashlib.sha256((variant + key).encode()).hexdigest() + '"'
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=31536000, immutable", "ETag": etag}
    if etag in request.headers.get('if-none-match', '').split(', ') or request.headers.get('if-none-match') == '*':
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type='image/webp' if preview and store.optimizer else store.content_type(key), headers=headers)


@app.get("/")
def root(request: Request, db=Depends(get_db)):
    user = session_user(request, db)
    if user:
        return RedirectResponse(f"/{quote(user['username'], safe='')}")
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/{username}")
def page(username: str):
    if username in {"login", "api", "media", "static"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class BrowserSpan(BaseModel):
    model_config = {"extra": "forbid"}
    name: Literal['browser.save','browser.fetch','browser.upload','browser.render','browser.page','browser.resource','browser.image.load','browser.image.decode']
    trace_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    span_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    parent_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    link_trace_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    link_span_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    start_ms: float = Field(ge=0, allow_inf_nan=False)
    end_ms: float = Field(ge=0, allow_inf_nan=False)
    route: Literal['/','/{username}','/api/me','/api/wishlists/{username}','/api/wishlists/{username}/items','/api/items/{item_id}','/api/items/{item_id}/images','/api/items/{item_id}/images/{photo_id}','/api/items/{item_id}/reserve','/api/items/{item_id}/release','/media/{key}','/static/{path}','/other'] = '/other'
    method: Literal['GET','POST','PUT','PATCH','DELETE','HEAD','OTHER'] = 'OTHER'
    status: int = Field(default=0, ge=0, le=599)
    error: bool = False

class BrowserBatch(BaseModel):
    model_config = {"extra": "forbid"}
    spans: list[BrowserSpan] = Field(max_length=32)

@app.post('/api/telemetry/traces', status_code=204)
def browser_traces(body: BrowserBatch):
    if not accept_browser_batch(): raise HTTPException(429, 'Telemetry budget exceeded')
    ingest_browser(body.spans)
    return Response(status_code=204)

app.add_middleware(RequestTelemetry)
