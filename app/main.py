import os
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import connect, guest
from app.storage import Store

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://wishlist.alchemmist.xyz").rstrip("/")
REQUIRED_GROUP = os.environ.get("AUTH_REQUIRED_GROUP", "app-wishlist-user")

app = FastAPI()
db = connect(DATA / "wishlist.sqlite")
store = Store(
    DATA,
    os.environ.get("S3_ENDPOINT", ""),
    os.environ.get("S3_BUCKET", "wishlist"),
    os.environ.get("S3_ACCESS_KEY", ""),
    os.environ.get("S3_SECRET_KEY", ""),
    os.environ.get("S3_PUBLIC_BASE", ""),
)


class ItemIn(BaseModel):
    title: str
    description: str = ""
    price: str = ""
    links: list[str] = []


class NameIn(BaseModel):
    name: str


def session_user(request: Request):
    sub = request.session.get("sub")
    if not sub:
        return None
    return db.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()


def require_owner(request: Request, username: str):
    user = session_user(request)
    if not user or user["username"].lower() != username.lower():
        raise HTTPException(401, "Sign in required")
    return user


def links_of(item_id: int) -> list[str]:
    return [row["url"] for row in db.execute("SELECT url FROM links WHERE item_id = ? ORDER BY id", (item_id,))]


def item_json(row, mine: bool) -> dict:
    reserved = db.execute("SELECT 1 FROM reservations WHERE item_id = ?", (row["id"],)).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "price": row["price"],
        "image": store.url(row["image_key"]),
        "links": links_of(row["id"]),
        "reserved": bool(reserved),
        "mine": mine,
    }


def safe_links(values: list[str]) -> list[str]:
    clean = []
    for value in values:
        value = value.strip()
        if value.startswith("https://") or value.startswith("http://"):
            clean.append(value)
    return clean


from starlette.middleware.sessions import SessionMiddleware

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-change-me"),
    https_only=PUBLIC_URL.startswith("https://"),
    same_site="lax",
)


@app.get("/api/me")
def me(request: Request):
    user = session_user(request)
    if not user:
        return {"user": None}
    return {"user": {"username": user["username"], "name": user["display_name"], "avatar": user["avatar"]}}


@app.get("/login")
async def login(request: Request):
    if not (ISSUER and CLIENT_ID and CLIENT_SECRET):
        raise HTTPException(503, "Sign-in is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        metadata = (await client.get(f"{ISSUER}/.well-known/openid-configuration")).json()
    request.session["oidc_state"] = os.urandom(16).hex()
    query = urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email groups",
        "redirect_uri": f"{PUBLIC_URL}/login/callback",
        "state": request.session["oidc_state"],
    })
    return RedirectResponse(f"{metadata['authorization_endpoint']}?{query}")


@app.get("/login/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    if not state or state != request.session.get("oidc_state"):
        raise HTTPException(400, "Bad login state")
    async with httpx.AsyncClient(timeout=10) as client:
        metadata = (await client.get(f"{ISSUER}/.well-known/openid-configuration")).json()
        token = (await client.post(metadata["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{PUBLIC_URL}/login/callback",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })).json()
        info = (await client.get(metadata["userinfo_endpoint"], headers={"Authorization": f"Bearer {token['access_token']}"})).json()
    groups = info.get("groups") or []
    if REQUIRED_GROUP and REQUIRED_GROUP not in groups:
        raise HTTPException(403, "This account is not in the wishlist group")
    username = (info.get("preferred_username") or info.get("nickname") or "").strip()
    if not username:
        raise HTTPException(400, "Authentik did not return a username")
    db.execute(
        """INSERT INTO users(sub, username, display_name, avatar) VALUES (?, ?, ?, ?)
           ON CONFLICT(sub) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, avatar=excluded.avatar""",
        (info["sub"], username, info.get("name") or username, info.get("picture") or info.get("avatar") or ""),
    )
    db.commit()
    request.session["sub"] = info["sub"]
    return RedirectResponse(f"/{username}")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/wishlists/{username}")
def wishlist(username: str, request: Request):
    owner = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if not owner:
        raise HTTPException(404, "No wishlist")
    viewer = session_user(request)
    mine = bool(viewer and viewer["id"] == owner["id"])
    rows = db.execute("SELECT * FROM items WHERE owner_id = ? ORDER BY position, id", (owner["id"],))
    return {
        "owner": {"username": owner["username"], "name": owner["display_name"], "avatar": owner["avatar"]},
        "mine": mine,
        "items": [item_json(row, mine) for row in rows],
    }


@app.post("/api/wishlists/{username}/items")
def add_item(username: str, body: ItemIn, request: Request):
    user = require_owner(request, username)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    cursor = db.execute(
        "INSERT INTO items(owner_id, title, description, price, position) VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(position),0)+1 FROM items WHERE owner_id = ?))",
        (user["id"], title, body.description.strip(), body.price.strip(), user["id"]),
    )
    item_id = cursor.lastrowid
    for url in safe_links(body.links):
        db.execute("INSERT INTO links(item_id, url) VALUES (?, ?)", (item_id, url))
    db.commit()
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return item_json(row, True)


@app.post("/api/items/{item_id}/image")
async def upload(item_id: int, request: Request, file: UploadFile = File(...)):
    row = db.execute("SELECT items.*, users.username FROM items JOIN users ON users.id = items.owner_id WHERE items.id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    require_owner(request, row["username"])
    data = await file.read()
    if not data or len(data) > 8_000_000:
        raise HTTPException(400, "Image must be under 8 MB")
    key = store.put(row["username"], data, file.content_type or "application/octet-stream")
    db.execute("UPDATE items SET image_key = ? WHERE id = ?", (key, item_id))
    db.commit()
    return {"image": store.url(key)}


@app.post("/api/items/{item_id}/reserve")
def reserve(item_id: int, body: NameIn):
    row = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Missing item")
    try:
        person = guest(db, body.name)
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
def release(item_id: int, body: NameIn):
    cleaned = " ".join(body.name.split())
    person = db.execute("SELECT id, name FROM guests WHERE name = ? COLLATE NOCASE", (cleaned,)).fetchone()
    current = db.execute("SELECT guest_id FROM reservations WHERE item_id = ?", (item_id,)).fetchone()
    if not person or not current or current["guest_id"] != person["id"]:
        raise HTTPException(403, "Name does not match")
    db.execute("DELETE FROM reservations WHERE item_id = ?", (item_id,))
    db.commit()
    return {"reserved": False}


@app.get("/media/{key:path}")
def media(key: str):
    if ".." in key.split("/"):
        raise HTTPException(404)
    data = store.read(key)
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="image/webp")


@app.get("/")
def root(request: Request):
    user = session_user(request)
    if user:
        return RedirectResponse(f"/{user['username']}")
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/{username}")
def page(username: str):
    if username in {"login", "api", "media", "static"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
