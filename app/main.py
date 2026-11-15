import html
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated
from uuid import UUID
from urllib.parse import urlsplit, quote

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

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

app = FastAPI()
DATABASE_PATH = DATA / "wishlist.sqlite"
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


def item_json(row, mine: bool, db, links=None, reserved=None) -> dict:
    if reserved is None:
        reserved = db.execute("SELECT 1 FROM reservations WHERE item_id = ?", (row["id"],)).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "price": row["price"],
        "image": store.url(row["image_key"]),
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
        return await oauth.authentik.authorize_redirect(request, f"{PUBLIC_URL}/login/callback")
    except (httpx.HTTPError, OAuthError, ValueError, KeyError):
        logger.exception("Could not start OIDC login")
        raise HTTPException(502, "Sign-in provider is unavailable. Please try again.")


@app.get("/login/callback")
async def callback(request: Request, db=Depends(get_db)):
    if not (ISSUER and CLIENT_ID and CLIENT_SECRET):
        raise HTTPException(503, "Sign-in is not configured")
    try:
        # Authlib checks state, PKCE, nonce, issuer, audience, expiry and JWT signature.
        token = await oauth.authentik.authorize_access_token(request)
        identity = token.get("userinfo")
        if not identity or not identity.get("sub"):
            raise ValueError("Missing verified ID token")
        metadata = await oauth.authentik.load_server_metadata()
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
    data = file.file.read(8_000_001)
    if not data or len(data) > 8_000_000:
        raise HTTPException(400, "Image must be under 8 MB")
    try:
        key = store.put(str(row["owner_id"]), data, file.content_type or "application/octet-stream")
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
            db.execute("UPDATE items SET image_key = ? WHERE id = ?", (key, item_id))
            db.commit()
        except Exception:
            db.rollback()
            store.delete(key)
            raise
        if previous["image_key"]:
            store.delete(previous["image_key"])
    attach_image()
    return {"image": store.url(key)}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    if row["image_key"]:
        store.delete(row["image_key"])
    return Response(status_code=204)


@app.delete("/api/items/{item_id}/image")
def delete_image(item_id: int, request: Request, db=Depends(get_db)):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["owner_id"], db)
    db.execute("UPDATE items SET image_key = NULL WHERE id = ?", (item_id,))
    db.commit()
    if row["image_key"]:
        store.delete(row["image_key"])
    return {"image": None}


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
def media(key: str):
    if ".." in key.split("/"):
        raise HTTPException(404)
    try:
        data = store.read(key)
    except Exception:
        logger.exception("Photo storage read failed")
        raise HTTPException(503, "Photo storage is unavailable")
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type=store.content_type(key), headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"})


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
